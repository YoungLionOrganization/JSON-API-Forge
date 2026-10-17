#include "GraphModel.hpp"

#include <QHash>
#include <QJsonValue>
#include <QQueue>
#include <QRegularExpression>
#include <QSet>
#include <QUuid>

#include <cmath>
#include <limits>
#include <utility>

namespace {
const QRegularExpression IdPattern(QStringLiteral(R"(^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$)"));
const QRegularExpression TypePattern(QStringLiteral(R"(^[a-z][a-z0-9]*(?:[._-][a-z0-9]+){1,7}$)"));
const QRegularExpression TargetPattern(QStringLiteral(R"(^config/[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.json$)"));

bool fail(QString *errorMessage, const QString &message)
{
    if (errorMessage != nullptr) {
        *errorMessage = message;
    }
    return false;
}

bool exactKeys(const QJsonObject &value, const QSet<QString> &allowed, QString *unknown)
{
    for (auto iterator = value.constBegin(); iterator != value.constEnd(); ++iterator) {
        if (!allowed.contains(iterator.key())) {
            if (unknown != nullptr) {
                *unknown = iterator.key();
            }
            return false;
        }
    }
    return true;
}

QString freshId(const QString &prefix)
{
    return prefix + u'-' + QUuid::createUuid().toString(QUuid::WithoutBraces);
}
} // namespace

GraphModel::GraphModel()
    : m_document(emptyDocument())
{
}

const QJsonObject &GraphModel::document() const
{
    return m_document;
}

QJsonObject GraphModel::emptyDocument(const QString &targetDocument)
{
    return QJsonObject{{QStringLiteral("$schema"), QStringLiteral("../../../schemas/editor-graph.schema.json")},
                       {QStringLiteral("schema_version"), 1},
                       {QStringLiteral("target_document"), targetDocument},
                       {QStringLiteral("metadata"),
                        QJsonObject{{QStringLiteral("name"), QStringLiteral("Forge operation graph")},
                                    {QStringLiteral("compiler"), QStringLiteral("json-api-forge-editor")}}},
                       {QStringLiteral("nodes"), QJsonArray{}},
                       {QStringLiteral("edges"), QJsonArray{}}};
}

bool GraphModel::setDocument(const QJsonObject &document, QString *errorMessage)
{
    const auto previous = m_document;
    m_document = document;
    if (!validate(errorMessage)) {
        m_document = previous;
        return false;
    }
    return true;
}

bool GraphModel::validate(QString *errorMessage) const
{
    static const QSet<QString> RootKeys{QStringLiteral("$schema"), QStringLiteral("schema_version"),
                                        QStringLiteral("target_document"), QStringLiteral("metadata"),
                                        QStringLiteral("nodes"), QStringLiteral("edges")};
    static const QSet<QString> NodeKeys{QStringLiteral("id"), QStringLiteral("type"), QStringLiteral("title"),
                                        QStringLiteral("x"), QStringLiteral("y"), QStringLiteral("properties")};
    static const QSet<QString> EdgeKeys{QStringLiteral("id"), QStringLiteral("from_node"), QStringLiteral("from_port"),
                                        QStringLiteral("to_node"), QStringLiteral("to_port")};
    QString unknown;
    if (!exactKeys(m_document, RootKeys, &unknown)) {
        return fail(errorMessage, QStringLiteral("Unknown graph field: %1").arg(unknown));
    }
    if (m_document.value(QStringLiteral("schema_version")).toInt(-1) != 1) {
        return fail(errorMessage, QStringLiteral("Graph schema_version must be 1."));
    }
    if (!TargetPattern.match(m_document.value(QStringLiteral("target_document")).toString()).hasMatch()) {
        return fail(errorMessage, QStringLiteral("target_document must be a direct config/*.json path."));
    }
    if (m_document.contains(QStringLiteral("metadata")) && !m_document.value(QStringLiteral("metadata")).isObject()) {
        return fail(errorMessage, QStringLiteral("Graph metadata must be an object."));
    }
    if (!m_document.value(QStringLiteral("nodes")).isArray() || !m_document.value(QStringLiteral("edges")).isArray()) {
        return fail(errorMessage, QStringLiteral("Graph nodes and edges must be arrays."));
    }
    const auto nodes = m_document.value(QStringLiteral("nodes")).toArray();
    const auto edges = m_document.value(QStringLiteral("edges")).toArray();
    if (nodes.size() > 500 || edges.size() > 2000) {
        return fail(errorMessage, QStringLiteral("Graph exceeds the 500 node / 2000 edge safety limit."));
    }

    QSet<QString> nodeIds;
    for (qsizetype index = 0; index < nodes.size(); ++index) {
        if (!nodes.at(index).isObject()) {
            return fail(errorMessage, QStringLiteral("Node %1 must be an object.").arg(index));
        }
        const auto node = nodes.at(index).toObject();
        if (!exactKeys(node, NodeKeys, &unknown)) {
            return fail(errorMessage, QStringLiteral("Node %1 has unknown field %2.").arg(index).arg(unknown));
        }
        const auto id = node.value(QStringLiteral("id")).toString();
        const auto type = node.value(QStringLiteral("type")).toString();
        const auto title = node.value(QStringLiteral("title"));
        const auto x = node.value(QStringLiteral("x")).toDouble(std::numeric_limits<double>::quiet_NaN());
        const auto y = node.value(QStringLiteral("y")).toDouble(std::numeric_limits<double>::quiet_NaN());
        if (!IdPattern.match(id).hasMatch() || nodeIds.contains(id)) {
            return fail(errorMessage, QStringLiteral("Node %1 has an unsafe or duplicate id.").arg(index));
        }
        if (!TypePattern.match(type).hasMatch()) {
            return fail(errorMessage, QStringLiteral("Node %1 has an unsafe type.").arg(index));
        }
        if ((!title.isUndefined() && !title.isString()) || title.toString().size() > 160) {
            return fail(errorMessage, QStringLiteral("Node %1 has an invalid title.").arg(index));
        }
        if (!std::isfinite(x) || !std::isfinite(y) || std::abs(x) > 1'000'000 || std::abs(y) > 1'000'000) {
            return fail(errorMessage, QStringLiteral("Node %1 has invalid coordinates.").arg(index));
        }
        if (!node.value(QStringLiteral("properties")).isObject()) {
            return fail(errorMessage, QStringLiteral("Node %1 properties must be an object.").arg(index));
        }
        nodeIds.insert(id);
    }

    QSet<QString> edgeIds;
    QSet<QString> incoming;
    QSet<QString> pairs;
    QHash<QString, QSet<QString>> adjacency;
    QHash<QString, int> indegree;
    for (const auto &id : std::as_const(nodeIds)) {
        indegree.insert(id, 0);
    }
    for (qsizetype index = 0; index < edges.size(); ++index) {
        if (!edges.at(index).isObject()) {
            return fail(errorMessage, QStringLiteral("Edge %1 must be an object.").arg(index));
        }
        const auto edge = edges.at(index).toObject();
        if (!exactKeys(edge, EdgeKeys, &unknown)) {
            return fail(errorMessage, QStringLiteral("Edge %1 has unknown field %2.").arg(index).arg(unknown));
        }
        const auto id = edge.value(QStringLiteral("id")).toString();
        const auto fromNode = edge.value(QStringLiteral("from_node")).toString();
        const auto fromPort = edge.value(QStringLiteral("from_port")).toString();
        const auto toNode = edge.value(QStringLiteral("to_node")).toString();
        const auto toPort = edge.value(QStringLiteral("to_port")).toString();
        if (!IdPattern.match(id).hasMatch() || !IdPattern.match(fromNode).hasMatch() || !IdPattern.match(fromPort).hasMatch()
            || !IdPattern.match(toNode).hasMatch() || !IdPattern.match(toPort).hasMatch()) {
            return fail(errorMessage, QStringLiteral("Edge %1 contains an unsafe id or port.").arg(index));
        }
        if (edgeIds.contains(id) || !nodeIds.contains(fromNode) || !nodeIds.contains(toNode) || fromNode == toNode) {
            return fail(errorMessage, QStringLiteral("Edge %1 is duplicate, self-referential, or references a missing node.").arg(index));
        }
        const auto inputKey = toNode + QChar::Null + toPort;
        const auto pair = fromNode + QChar::Null + fromPort + QChar::Null + toNode + QChar::Null + toPort;
        if (incoming.contains(inputKey) || pairs.contains(pair)) {
            return fail(errorMessage, QStringLiteral("Edge %1 duplicates a connection or targets an occupied input.").arg(index));
        }
        edgeIds.insert(id);
        incoming.insert(inputKey);
        pairs.insert(pair);
        if (!adjacency[fromNode].contains(toNode)) {
            adjacency[fromNode].insert(toNode);
            indegree[toNode] = indegree.value(toNode) + 1;
        }
    }

    QQueue<QString> ready;
    for (auto iterator = indegree.constBegin(); iterator != indegree.constEnd(); ++iterator) {
        if (iterator.value() == 0) {
            ready.enqueue(iterator.key());
        }
    }
    int visited = 0;
    while (!ready.isEmpty()) {
        const auto current = ready.dequeue();
        ++visited;
        for (const auto &target : adjacency.value(current)) {
            indegree[target] = indegree.value(target) - 1;
            if (indegree.value(target) == 0) {
                ready.enqueue(target);
            }
        }
    }
    if (visited != nodeIds.size()) {
        return fail(errorMessage, QStringLiteral("Execution connections must form an acyclic graph."));
    }
    return true;
}

QStringList GraphModel::topologicalOrder(QString *errorMessage) const
{
    if (!validate(errorMessage)) {
        return {};
    }
    const auto nodes = m_document.value(QStringLiteral("nodes")).toArray();
    const auto edges = m_document.value(QStringLiteral("edges")).toArray();
    QHash<QString, int> indegree;
    QHash<QString, QStringList> adjacency;
    for (const auto &value : nodes) {
        indegree.insert(value.toObject().value(QStringLiteral("id")).toString(), 0);
    }
    for (const auto &value : edges) {
        const auto edge = value.toObject();
        const auto from = edge.value(QStringLiteral("from_node")).toString();
        const auto to = edge.value(QStringLiteral("to_node")).toString();
        if (!adjacency[from].contains(to)) {
            adjacency[from].append(to);
            indegree[to] = indegree.value(to) + 1;
        }
    }
    QStringList ready;
    for (auto iterator = indegree.constBegin(); iterator != indegree.constEnd(); ++iterator) {
        if (iterator.value() == 0) {
            ready.append(iterator.key());
        }
    }
    ready.sort();
    QStringList result;
    while (!ready.isEmpty()) {
        const auto current = ready.takeFirst();
        result.append(current);
        auto targets = adjacency.value(current);
        targets.sort();
        for (const auto &target : targets) {
            indegree[target] = indegree.value(target) - 1;
            if (indegree.value(target) == 0) {
                ready.append(target);
                ready.sort();
            }
        }
    }
    return result;
}

QString GraphModel::addNode(const QString &type, const QString &title, const QPointF &position, const QJsonObject &properties)
{
    auto nodes = m_document.value(QStringLiteral("nodes")).toArray();
    const auto id = freshId(QStringLiteral("node"));
    nodes.append(QJsonObject{{QStringLiteral("id"), id},
                             {QStringLiteral("type"), type},
                             {QStringLiteral("title"), title.left(160)},
                             {QStringLiteral("x"), qBound(-1'000'000.0, position.x(), 1'000'000.0)},
                             {QStringLiteral("y"), qBound(-1'000'000.0, position.y(), 1'000'000.0)},
                             {QStringLiteral("properties"), properties}});
    m_document.insert(QStringLiteral("nodes"), nodes);
    QString ignored;
    if (!validate(&ignored)) {
        nodes.removeLast();
        m_document.insert(QStringLiteral("nodes"), nodes);
        return {};
    }
    return id;
}

bool GraphModel::updateNode(const QString &id, const QString &title, const QJsonObject &properties, QString *errorMessage)
{
    auto nodes = m_document.value(QStringLiteral("nodes")).toArray();
    for (qsizetype index = 0; index < nodes.size(); ++index) {
        auto nodeValue = nodes.at(index).toObject();
        if (nodeValue.value(QStringLiteral("id")).toString() != id) {
            continue;
        }
        const auto previous = nodeValue;
        nodeValue.insert(QStringLiteral("title"), title);
        nodeValue.insert(QStringLiteral("properties"), properties);
        nodes.replace(index, nodeValue);
        m_document.insert(QStringLiteral("nodes"), nodes);
        if (!validate(errorMessage)) {
            nodes.replace(index, previous);
            m_document.insert(QStringLiteral("nodes"), nodes);
            return false;
        }
        return true;
    }
    return fail(errorMessage, QStringLiteral("Node no longer exists."));
}

bool GraphModel::moveNode(const QString &id, const QPointF &position)
{
    auto nodes = m_document.value(QStringLiteral("nodes")).toArray();
    for (qsizetype index = 0; index < nodes.size(); ++index) {
        auto value = nodes.at(index).toObject();
        if (value.value(QStringLiteral("id")).toString() == id) {
            value.insert(QStringLiteral("x"), qBound(-1'000'000.0, position.x(), 1'000'000.0));
            value.insert(QStringLiteral("y"), qBound(-1'000'000.0, position.y(), 1'000'000.0));
            nodes.replace(index, value);
            m_document.insert(QStringLiteral("nodes"), nodes);
            return true;
        }
    }
    return false;
}

bool GraphModel::removeNode(const QString &id)
{
    auto nodes = m_document.value(QStringLiteral("nodes")).toArray();
    bool removed = false;
    for (qsizetype index = nodes.size(); index > 0; --index) {
        if (nodes.at(index - 1).toObject().value(QStringLiteral("id")).toString() == id) {
            nodes.removeAt(index - 1);
            removed = true;
        }
    }
    if (!removed) {
        return false;
    }
    auto edges = m_document.value(QStringLiteral("edges")).toArray();
    for (qsizetype index = edges.size(); index > 0; --index) {
        const auto edge = edges.at(index - 1).toObject();
        if (edge.value(QStringLiteral("from_node")).toString() == id || edge.value(QStringLiteral("to_node")).toString() == id) {
            edges.removeAt(index - 1);
        }
    }
    m_document.insert(QStringLiteral("nodes"), nodes);
    m_document.insert(QStringLiteral("edges"), edges);
    return true;
}

bool GraphModel::connectNodes(const QString &fromNode, const QString &fromPort, const QString &toNode, const QString &toPort,
                              QString *errorMessage)
{
    auto edges = m_document.value(QStringLiteral("edges")).toArray();
    edges.append(QJsonObject{{QStringLiteral("id"), freshId(QStringLiteral("edge"))},
                             {QStringLiteral("from_node"), fromNode},
                             {QStringLiteral("from_port"), fromPort},
                             {QStringLiteral("to_node"), toNode},
                             {QStringLiteral("to_port"), toPort}});
    m_document.insert(QStringLiteral("edges"), edges);
    if (!validate(errorMessage)) {
        edges.removeLast();
        m_document.insert(QStringLiteral("edges"), edges);
        return false;
    }
    return true;
}

bool GraphModel::removeEdge(const QString &id)
{
    auto edges = m_document.value(QStringLiteral("edges")).toArray();
    for (qsizetype index = 0; index < edges.size(); ++index) {
        if (edges.at(index).toObject().value(QStringLiteral("id")).toString() == id) {
            edges.removeAt(index);
            m_document.insert(QStringLiteral("edges"), edges);
            return true;
        }
    }
    return false;
}

QJsonObject GraphModel::node(const QString &id) const
{
    for (const auto &value : m_document.value(QStringLiteral("nodes")).toArray()) {
        const auto object = value.toObject();
        if (object.value(QStringLiteral("id")).toString() == id) {
            return object;
        }
    }
    return {};
}

QJsonObject GraphModel::compiledFragment(QString *errorMessage) const
{
    const auto order = topologicalOrder(errorMessage);
    if (order.isEmpty()) {
        return {};
    }
    QJsonObject operationNode;
    QJsonObject requestProperties;
    QJsonObject policyProperties;
    QJsonArray statements;
    QJsonArray hooks;
    bool hasExecute = false;
    bool hasRequest = false;
    bool hasPolicy = false;
    bool hasResponse = false;
    for (const auto &id : order) {
        const auto value = node(id);
        const auto type = value.value(QStringLiteral("type")).toString();
        const auto properties = value.value(QStringLiteral("properties")).toObject();
        if (type == QStringLiteral("operation.call")) {
            if (!operationNode.isEmpty()) {
                fail(errorMessage, QStringLiteral("A compiled graph currently supports exactly one Operation node."));
                return {};
            }
            operationNode = value;
        } else if (type == QStringLiteral("request.input")) {
            if (hasRequest) {
                fail(errorMessage, QStringLiteral("A compiled graph supports at most one Request node."));
                return {};
            }
            hasRequest = true;
            requestProperties = properties;
        } else if (type == QStringLiteral("auth.policy")) {
            if (hasPolicy) {
                fail(errorMessage, QStringLiteral("A compiled graph supports at most one Authorization node."));
                return {};
            }
            hasPolicy = true;
            policyProperties = properties;
        } else if (type == QStringLiteral("data.query") || type == QStringLiteral("data.mutate")) {
            const auto sql = properties.value(QStringLiteral("sql")).toString().trimmed();
            if (sql.isEmpty()) {
                fail(errorMessage, QStringLiteral("Every Query/Mutation node needs a non-empty sql property."));
                return {};
            }
            const auto defaultMode = type == QStringLiteral("data.mutate") ? QStringLiteral("execute") : QStringLiteral("fetch_all");
            const auto mode = properties.value(QStringLiteral("mode")).toString(defaultMode);
            if (!QSet<QString>{QStringLiteral("execute"), QStringLiteral("fetch_one"), QStringLiteral("fetch_all"),
                               QStringLiteral("scalar")}
                     .contains(mode)) {
                fail(errorMessage, QStringLiteral("SQL node mode is not supported."));
                return {};
            }
            QJsonObject statement{{QStringLiteral("sql"), sql}, {QStringLiteral("mode"), mode}};
            if (properties.value(QStringLiteral("params")).isObject()) {
                statement.insert(QStringLiteral("params"), properties.value(QStringLiteral("params")));
            }
            if (!properties.value(QStringLiteral("result_name")).toString().isEmpty()) {
                statement.insert(QStringLiteral("result_name"), properties.value(QStringLiteral("result_name")));
            }
            if (properties.value(QStringLiteral("max_rows")).isDouble()) {
                statement.insert(QStringLiteral("max_rows"), properties.value(QStringLiteral("max_rows")));
            }
            for (const auto &key : {QStringLiteral("require_rowcount_min"), QStringLiteral("require_rowcount_max")}) {
                if (!properties.contains(key)) { continue; }
                const auto bound = properties.value(key);
                if (!bound.isNull() && (!bound.isDouble() || bound.toInteger(-1) < 0 || QJsonValue(bound.toInteger(-1)) != bound)) {
                    fail(errorMessage, QStringLiteral("SQL node %1 must be a nonnegative integer or null.").arg(key));
                    return {};
                }
                statement.insert(key, bound);
            }
            if (!statement.value(QStringLiteral("require_rowcount_min")).isNull()
                && !statement.value(QStringLiteral("require_rowcount_max")).isNull()
                && statement.contains(QStringLiteral("require_rowcount_min")) && statement.contains(QStringLiteral("require_rowcount_max"))
                && statement.value(QStringLiteral("require_rowcount_min")).toInteger() > statement.value(QStringLiteral("require_rowcount_max")).toInteger()) {
                fail(errorMessage, QStringLiteral("SQL node require_rowcount_min cannot exceed require_rowcount_max."));
                return {};
            }
            statements.append(statement);
            hasExecute = hasExecute || mode == QStringLiteral("execute");
        } else if (type == QStringLiteral("python.call")) {
            const auto hook = properties.value(QStringLiteral("hook")).toString().trimmed();
            if (!hook.isEmpty()) {
                hooks.append(hook);
            }
        } else if (type == QStringLiteral("response.output")) {
            if (hasResponse) {
                fail(errorMessage, QStringLiteral("A compiled graph supports at most one Response node."));
                return {};
            }
            hasResponse = true;
            const auto status = properties.value(QStringLiteral("status_code")).toInt(200);
            if (status != 200) {
                fail(errorMessage, QStringLiteral("Forge operations currently compile only the standard HTTP 200 response."));
                return {};
            }
        } else {
            fail(errorMessage,
                 QStringLiteral("Node type '%1' is design-only until a Forge compiler implementation is available; it was not silently omitted.")
                     .arg(type));
            return {};
        }
    }
    if (operationNode.isEmpty() || statements.isEmpty()) {
        fail(errorMessage, QStringLiteral("Compilation needs one Operation node and at least one Query/Mutation node."));
        return {};
    }

    const auto operationProperties = operationNode.value(QStringLiteral("properties")).toObject();
    const auto method = operationProperties.value(QStringLiteral("method"))
                            .toString(requestProperties.value(QStringLiteral("method")).toString(hasExecute ? QStringLiteral("POST")
                                                                                                      : QStringLiteral("GET")))
                            .toUpper();
    if (!QSet<QString>{QStringLiteral("GET"), QStringLiteral("POST"), QStringLiteral("PUT"), QStringLiteral("PATCH"),
                       QStringLiteral("DELETE")}
             .contains(method)) {
        fail(errorMessage, QStringLiteral("Operation method must be GET, POST, PUT, PATCH, or DELETE."));
        return {};
    }
    QJsonObject operation{{QStringLiteral("name"),
                           operationProperties.value(QStringLiteral("name")).toString(QStringLiteral("graph.operation"))},
                          {QStringLiteral("method"), method},
                          {QStringLiteral("database"),
                           operationProperties.value(QStringLiteral("database")).toString(QStringLiteral("primary"))},
                          {QStringLiteral("transaction"), hasExecute},
                          {QStringLiteral("statements"), statements}};
    const bool isPublic = policyProperties.value(QStringLiteral("public")).toBool(false);
    if (isPublic) {
        operation.insert(QStringLiteral("public"), true);
    } else {
        operation.insert(QStringLiteral("permission"),
                         policyProperties.value(QStringLiteral("permission")).toString(QStringLiteral("graph.execute")));
    }
    if (hasExecute && operationProperties.value(QStringLiteral("idempotency")).toBool(false)) {
        operation.insert(QStringLiteral("idempotency"), true);
    }
    if (requestProperties.value(QStringLiteral("input_schema")).isObject()) {
        operation.insert(QStringLiteral("input_schema"), requestProperties.value(QStringLiteral("input_schema")));
    }
    if (!operationProperties.value(QStringLiteral("summary")).toString().isEmpty()) {
        operation.insert(QStringLiteral("summary"), operationProperties.value(QStringLiteral("summary")));
    }
    for (const auto &key : {QStringLiteral("invalidate_resources"), QStringLiteral("invalidate_operations")}) {
        if (!operationProperties.contains(key)) { continue; }
        const auto targets = operationProperties.value(key);
        if (!targets.isArray()) {
            fail(errorMessage, QStringLiteral("Operation %1 must be an array of strings.").arg(key));
            return {};
        }
        for (const auto &target : targets.toArray()) {
            if (!target.isString()) {
                fail(errorMessage, QStringLiteral("Operation %1 must be an array of strings.").arg(key));
                return {};
            }
        }
        operation.insert(key, targets);
    }
    if (!hooks.isEmpty()) {
        operation.insert(QStringLiteral("background_hooks"), hooks);
    }
    return QJsonObject{{QStringLiteral("$schema"), QStringLiteral("../../../schemas/fragment.schema.json")},
                       {QStringLiteral("operations"), QJsonArray{operation}}};
}
