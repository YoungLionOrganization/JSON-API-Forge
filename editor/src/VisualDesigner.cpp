#include "VisualDesigner.hpp"

#include <QAbstractItemView>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFormLayout>
#include <QHeaderView>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QLabel>
#include <QListWidget>
#include <QMimeData>
#include <QSplitter>
#include <QTableWidget>
#include <QTreeWidget>
#include <QVBoxLayout>

#include <functional>

namespace {
const auto ComponentMime = QStringLiteral("application/x-json-api-forge-component");
constexpr auto CollectionRole = Qt::UserRole + 1;
constexpr auto IndexRole = Qt::UserRole + 2;

class PaletteList final : public QListWidget {
public:
    using QListWidget::QListWidget;

protected:
    [[nodiscard]] QStringList mimeTypes() const override { return {ComponentMime}; }

    [[nodiscard]] QMimeData *mimeData(const QList<QListWidgetItem *> &items) const override
    {
        auto *mime = new QMimeData;
        if (!items.isEmpty()) {
            mime->setData(ComponentMime, items.first()->data(Qt::UserRole).toByteArray());
        }
        return mime;
    }
};

class CanvasTree final : public QTreeWidget {
public:
    using QTreeWidget::QTreeWidget;
    std::function<void(const QByteArray &)> componentDropped;

protected:
    void dragEnterEvent(QDragEnterEvent *event) override
    {
        if (event->mimeData()->hasFormat(ComponentMime)) {
            event->acceptProposedAction();
            return;
        }
        QTreeWidget::dragEnterEvent(event);
    }

    void dragMoveEvent(QDragMoveEvent *event) override
    {
        if (event->mimeData()->hasFormat(ComponentMime)) {
            event->acceptProposedAction();
            return;
        }
        QTreeWidget::dragMoveEvent(event);
    }

    void dropEvent(QDropEvent *event) override
    {
        if (event->mimeData()->hasFormat(ComponentMime) && componentDropped) {
            componentDropped(event->mimeData()->data(ComponentMime));
            event->acceptProposedAction();
            return;
        }
        QTreeWidget::dropEvent(event);
    }
};

QString valueText(const QJsonValue &value)
{
    if (value.isString()) {
        return value.toString();
    }
    if (value.isBool()) {
        return value.toBool() ? QStringLiteral("true") : QStringLiteral("false");
    }
    if (value.isDouble()) {
        return QString::number(value.toDouble(), 'g', 15);
    }
    if (value.isNull() || value.isUndefined()) {
        return QStringLiteral("null");
    }
    if (value.isObject()) {
        return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact));
    }
    return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact));
}

bool parseValue(const QString &text, const QJsonValue &original, QJsonValue *value)
{
    if (original.isString()) { *value = text; return true; }
    QJsonParseError error;
    const auto parsed = QJsonDocument::fromJson(QByteArray("{\"value\":") + text.trimmed().toUtf8() + QByteArray("}"), &error);
    if (error.error != QJsonParseError::NoError || !parsed.isObject()) { return false; }
    const auto candidate = parsed.object().value(QStringLiteral("value"));
    if (candidate.type() != original.type()) { return false; }
    *value = candidate;
    return true;
}
} // namespace

VisualDesigner::VisualDesigner(QWidget *parent)
    : QWidget(parent)
    , m_palette(new PaletteList(this))
    , m_canvas(new CanvasTree(this))
    , m_properties(new QTableWidget(this))
{
    setObjectName(QStringLiteral("visualDesigner"));
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    auto *splitter = new QSplitter(this);
    splitter->setChildrenCollapsible(false);
    splitter->setHandleWidth(1);

    auto *palettePanel = new QWidget(splitter);
    auto *paletteLayout = new QVBoxLayout(palettePanel);
    paletteLayout->setContentsMargins(12, 12, 12, 12);
    auto *paletteTitle = new QLabel(QStringLiteral("COMPONENTS"), palettePanel);
    paletteTitle->setObjectName(QStringLiteral("panelEyebrow"));
    paletteLayout->addWidget(paletteTitle);
    m_palette->setObjectName(QStringLiteral("componentPalette"));
    m_palette->setDragEnabled(true);
    m_palette->setDragDropMode(QAbstractItemView::DragOnly);
    m_palette->setSpacing(5);
    paletteLayout->addWidget(m_palette);

    m_canvas->setObjectName(QStringLiteral("designerCanvas"));
    m_canvas->setHeaderLabels({QStringLiteral("APP STRUCTURE"), QStringLiteral("TYPE")});
    m_canvas->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    m_canvas->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_canvas->setAcceptDrops(true);
    m_canvas->setDragDropMode(QAbstractItemView::DropOnly);
    m_canvas->setRootIsDecorated(true);
    static_cast<CanvasTree *>(m_canvas)->componentDropped = [this](const QByteArray &payload) { insertTemplate(payload); };

    auto *propertyPanel = new QWidget(splitter);
    auto *propertyLayout = new QVBoxLayout(propertyPanel);
    propertyLayout->setContentsMargins(12, 12, 12, 12);
    auto *propertyTitle = new QLabel(QStringLiteral("PROPERTIES"), propertyPanel);
    propertyTitle->setObjectName(QStringLiteral("panelEyebrow"));
    propertyLayout->addWidget(propertyTitle);
    m_properties->setObjectName(QStringLiteral("propertyTable"));
    m_properties->setColumnCount(2);
    m_properties->setHorizontalHeaderLabels({QStringLiteral("Key"), QStringLiteral("Value")});
    m_properties->horizontalHeader()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    m_properties->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_properties->verticalHeader()->hide();
    propertyLayout->addWidget(m_properties);

    splitter->addWidget(palettePanel);
    splitter->addWidget(m_canvas);
    splitter->addWidget(propertyPanel);
    splitter->setSizes({220, 620, 320});
    layout->addWidget(splitter);

    connect(m_canvas, &QTreeWidget::currentItemChanged, this, [this] { showProperties(); });
    connect(m_properties, &QTableWidget::cellChanged, this, &VisualDesigner::applyPropertyEdit);
    connect(m_palette, &QListWidget::itemDoubleClicked, this, [this](QListWidgetItem *item) {
        insertTemplate(item->data(Qt::UserRole).toByteArray());
    });
    installBuiltInComponents();
}

void VisualDesigner::installBuiltInComponents()
{
    addPaletteComponent(
        QStringLiteral("SQL Resource"), QStringLiteral("resources"),
        QJsonObject{{QStringLiteral("database"), QStringLiteral("primary")},
                    {QStringLiteral("table"), QStringLiteral("items")},
                    {QStringLiteral("path"), QStringLiteral("items")},
                    {QStringLiteral("auto_create"), false},
                    {QStringLiteral("columns"), QJsonObject{}},
                    {QStringLiteral("allowed_actions"), QJsonArray{QStringLiteral("list"), QStringLiteral("read")}}});
    addPaletteComponent(
        QStringLiteral("Operation / RPC"), QStringLiteral("operations"),
        QJsonObject{{QStringLiteral("name"), QStringLiteral("operation.name")},
                    {QStringLiteral("method"), QStringLiteral("POST")},
                    {QStringLiteral("permission"), QStringLiteral("operation.execute")},
                    {QStringLiteral("statements"), QJsonArray{QJsonObject{{QStringLiteral("sql"), QStringLiteral("SELECT 1")},
                                                                          {QStringLiteral("mode"), QStringLiteral("scalar")}}}}});
    addPaletteComponent(QStringLiteral("Static Data Source"), QStringLiteral("data_sources"),
                        QJsonObject{{QStringLiteral("name"), QStringLiteral("public-info")},
                                    {QStringLiteral("type"), QStringLiteral("static")},
                                    {QStringLiteral("public"), true},
                                    {QStringLiteral("data"), QJsonObject{{QStringLiteral("status"), QStringLiteral("ok")}}}});
    addPaletteComponent(QStringLiteral("Realtime Channel"), QStringLiteral("event_channels"),
                        QJsonObject{{QStringLiteral("name"), QStringLiteral("updates")},
                                    {QStringLiteral("publish_permission"), QStringLiteral("events.publish")},
                                    {QStringLiteral("subscribe_permission"), QStringLiteral("events.subscribe")}});
}

void VisualDesigner::addPaletteComponent(const QString &label, const QString &collection, const QJsonObject &value)
{
    const QJsonObject payload{{QStringLiteral("collection"), collection}, {QStringLiteral("value"), value}};
    auto *item = new QListWidgetItem(label, m_palette);
    item->setToolTip(QStringLiteral("Double-click or drag onto the app structure"));
    item->setData(Qt::UserRole, QJsonDocument(payload).toJson(QJsonDocument::Compact));
}

void VisualDesigner::setDocument(const QJsonObject &document)
{
    m_document = document;
    refreshCanvas();
}

QJsonObject VisualDesigner::document() const
{
    return m_document;
}

void VisualDesigner::refreshCanvas()
{
    m_refreshing = true;
    m_canvas->clear();
    const auto keys = m_document.keys();
    for (const auto &key : keys) {
        const auto value = m_document.value(key);
        auto *root = new QTreeWidgetItem(m_canvas, {key, value.isArray() ? QStringLiteral("array") : QStringLiteral("object")});
        root->setData(0, CollectionRole, key);
        root->setData(0, IndexRole, -1);
        if (value.isArray()) {
            const auto array = value.toArray();
            for (qsizetype index = 0; index < array.size(); ++index) {
                const auto object = array.at(index).toObject();
                const auto label = object.value(QStringLiteral("name")).toString(
                    object.value(QStringLiteral("path")).toString(QStringLiteral("Item %1").arg(index + 1)));
                auto *child = new QTreeWidgetItem(root, {label, QStringLiteral("object")});
                child->setData(0, CollectionRole, key);
                child->setData(0, IndexRole, index);
            }
        }
        root->setExpanded(true);
    }
    m_refreshing = false;
    showProperties();
}

QJsonObject VisualDesigner::selectedObject() const
{
    const auto *item = m_canvas->currentItem();
    if (item == nullptr) {
        return {};
    }
    const auto collection = item->data(0, CollectionRole).toString();
    const auto index = item->data(0, IndexRole).toLongLong();
    if (index >= 0) {
        return m_document.value(collection).toArray().at(index).toObject();
    }
    return m_document.value(collection).toObject();
}

void VisualDesigner::showProperties()
{
    m_refreshing = true;
    m_properties->setRowCount(0);
    const auto object = selectedObject();
    const auto keys = object.keys();
    for (const auto &key : keys) {
        const auto row = m_properties->rowCount();
        m_properties->insertRow(row);
        auto *keyItem = new QTableWidgetItem(key);
        keyItem->setFlags(keyItem->flags() & ~Qt::ItemIsEditable);
        m_properties->setItem(row, 0, keyItem);
        m_properties->setItem(row, 1, new QTableWidgetItem(valueText(object.value(key))));
    }
    m_refreshing = false;
}

void VisualDesigner::applyPropertyEdit(int row, int column)
{
    if (m_refreshing || column != 1 || m_properties->item(row, 0) == nullptr || m_properties->item(row, 1) == nullptr) {
        return;
    }
    auto object = selectedObject();
    if (object.isEmpty()) {
        return;
    }
    const auto key = m_properties->item(row, 0)->text();
    QJsonValue value;
    if (!parseValue(m_properties->item(row, 1)->text(), object.value(key), &value)) {
        emit statusMessage(QStringLiteral("Enter valid JSON of the existing property type. Use Code mode to change types."));
        showProperties();
        return;
    }
    object.insert(key, value);
    replaceSelectedObject(object);
    auto *selected = m_canvas->currentItem();
    if (selected != nullptr && selected->data(0, IndexRole).toLongLong() >= 0) {
        selected->setText(0, object.value(QStringLiteral("name")).toString(
            object.value(QStringLiteral("path")).toString(QStringLiteral("Item %1").arg(selected->data(0, IndexRole).toLongLong() + 1))));
    }
    emit documentChanged(m_document);
}

void VisualDesigner::replaceSelectedObject(const QJsonObject &value)
{
    const auto *item = m_canvas->currentItem();
    if (item == nullptr) {
        return;
    }
    const auto collection = item->data(0, CollectionRole).toString();
    const auto index = item->data(0, IndexRole).toLongLong();
    if (index >= 0) {
        auto array = m_document.value(collection).toArray();
        array.replace(index, value);
        m_document.insert(collection, array);
    } else {
        m_document.insert(collection, value);
    }
}

void VisualDesigner::insertTemplate(const QByteArray &payload)
{
    QJsonParseError error;
    const auto document = QJsonDocument::fromJson(payload, &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) {
        emit statusMessage(QStringLiteral("Plugin component payload is invalid."));
        return;
    }
    const auto collection = document.object().value(QStringLiteral("collection")).toString();
    const auto value = document.object().value(QStringLiteral("value")).toObject();
    if (collection.isEmpty() || value.isEmpty()) {
        emit statusMessage(QStringLiteral("Component does not declare a target collection."));
        return;
    }
    const auto existing = m_document.value(collection);
    if (!existing.isUndefined() && !existing.isArray()) {
        emit statusMessage(QStringLiteral("The target collection is not an array; existing data was preserved."));
        return;
    }
    auto array = existing.toArray();
    array.append(value);
    m_document.insert(collection, array);
    refreshCanvas();
    emit documentChanged(m_document);
    emit statusMessage(QStringLiteral("Added %1 component.").arg(collection));
}
