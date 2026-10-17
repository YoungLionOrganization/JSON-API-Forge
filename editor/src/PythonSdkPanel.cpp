#include "PythonSdkPanel.hpp"

#include "ApiClient.hpp"

#include <QApplication>
#include <QClipboard>
#include <QComboBox>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonValue>
#include <QLabel>
#include <QLineEdit>
#include <QPlainTextEdit>
#include <QProcess>
#include <QPushButton>
#include <QRegularExpression>
#include <QSet>
#include <QStandardPaths>
#include <QTimer>
#include <QUrl>
#include <QVBoxLayout>

namespace {
bool fail(QString *errorMessage, const QString &message)
{
    if (errorMessage != nullptr) {
        *errorMessage = message;
    }
    return false;
}

QString pythonLiteral(const QString &value)
{
    auto encoded = QJsonDocument(QJsonArray{value}).toJson(QJsonDocument::Compact);
    encoded.remove(0, 1);
    encoded.chop(1);
    return QString::fromUtf8(encoded);
}

bool safeEndpoint(const QString &value, QString *errorMessage)
{
    QUrl normalized;
    return ApiClient::normalizeServerUrl(QUrl(value), true, &normalized, errorMessage);
}

QString pythonEndpoint(const QString &value)
{
    // safeEndpoint has already restricted plain HTTP to an explicit loopback URL.
    return pythonLiteral(value) + (QUrl(value).scheme().compare(QStringLiteral("http"), Qt::CaseInsensitive) == 0
        ? QStringLiteral(", allow_insecure_http=True") : QString());
}
} // namespace

PythonSdkPanel::PythonSdkPanel(QWidget *parent)
    : QWidget(parent)
    , m_mode(new QComboBox(this))
    , m_endpoint(new QLineEdit(QStringLiteral("https://forge.example.com"), this))
    , m_clusterEndpoints(new QLineEdit(QStringLiteral("https://forge-eu.example.com, https://forge-us.example.com"), this))
    , m_project(new QLineEdit(QStringLiteral("my-project"), this))
    , m_resource(new QLineEdit(QStringLiteral("records"), this))
    , m_operation(new QLineEdit(QStringLiteral("records.summary"), this))
    , m_apiKeyEnvironment(new QLineEdit(QStringLiteral("FORGE_API_KEY"), this))
    , m_install(new QLabel(this))
    , m_result(new QLabel(QStringLiteral("Package and connection checks have not run."), this))
    , m_snippet(new QPlainTextEdit(this))
{
    setObjectName(QStringLiteral("pythonSdkPanel"));
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(14, 14, 14, 14);
    auto *title = new QLabel(QStringLiteral("PYTHON SDK INTEGRATION"), this);
    title->setObjectName(QStringLiteral("panelEyebrow"));
    layout->addWidget(title);
    auto *intro = new QLabel(
        QStringLiteral("Generate production-oriented sync, async, cluster, YoungLion or DDM integration code. Secrets are read from an environment variable and are never written into the snippet."),
        this);
    intro->setWordWrap(true);
    intro->setObjectName(QStringLiteral("mutedText"));
    layout->addWidget(intro);

    m_mode->addItem(QStringLiteral("Sync client"), QStringLiteral("sync"));
    m_mode->addItem(QStringLiteral("Async client"), QStringLiteral("async"));
    m_mode->addItem(QStringLiteral("Multi-region cluster"), QStringLiteral("cluster"));
    m_mode->addItem(QStringLiteral("YoungLion native"), QStringLiteral("younglion"));
    m_mode->addItem(QStringLiteral("DDM adapter"), QStringLiteral("ddm"));
    auto *form = new QFormLayout;
    form->addRow(QStringLiteral("Integration mode"), m_mode);
    form->addRow(QStringLiteral("Forge endpoint"), m_endpoint);
    form->addRow(QStringLiteral("Cluster endpoints"), m_clusterEndpoints);
    form->addRow(QStringLiteral("Project slug"), m_project);
    form->addRow(QStringLiteral("Resource route"), m_resource);
    form->addRow(QStringLiteral("Operation"), m_operation);
    form->addRow(QStringLiteral("API key env var"), m_apiKeyEnvironment);
    layout->addLayout(form);
    m_install->setObjectName(QStringLiteral("sdkInstallCard"));
    m_install->setTextInteractionFlags(Qt::TextSelectableByMouse);
    layout->addWidget(m_install);
    m_result->setObjectName(QStringLiteral("policyCard"));
    m_result->setWordWrap(true);
    layout->addWidget(m_result);
    m_snippet->setObjectName(QStringLiteral("sdkSnippet"));
    m_snippet->setReadOnly(true);
    layout->addWidget(m_snippet, 1);
    auto *buttons = new QHBoxLayout;
    auto *check = new QPushButton(QStringLiteral("Check installed SDK"), this);
    auto *health = new QPushButton(QStringLiteral("Run health check"), this);
    auto *copy = new QPushButton(QStringLiteral("Copy snippet"), this);
    copy->setObjectName(QStringLiteral("primaryButton"));
    buttons->addWidget(check);
    buttons->addWidget(health);
    buttons->addStretch();
    buttons->addWidget(copy);
    layout->addLayout(buttons);

    const QList<QLineEdit *> fields{m_endpoint, m_clusterEndpoints, m_project, m_resource, m_operation, m_apiKeyEnvironment};
    for (auto *field : fields) {
        connect(field, &QLineEdit::textChanged, this, &PythonSdkPanel::refreshSnippet);
    }
    connect(m_mode, &QComboBox::currentIndexChanged, this, &PythonSdkPanel::refreshSnippet);
    connect(copy, &QPushButton::clicked, this, &PythonSdkPanel::copySnippet);
    connect(check, &QPushButton::clicked, this, &PythonSdkPanel::checkPackage);
    connect(health, &QPushButton::clicked, this, &PythonSdkPanel::runHealthCheck);
    refreshSnippet();
}

PythonSdkSettings PythonSdkPanel::settings() const
{
    return PythonSdkSettings{m_mode->currentData().toString(), m_endpoint->text().trimmed(), m_clusterEndpoints->text().trimmed(),
                             m_project->text().trimmed(), m_resource->text().trimmed(), m_operation->text().trimmed(),
                             m_apiKeyEnvironment->text().trimmed()};
}

QString PythonSdkPanel::generatedSnippet(const PythonSdkSettings &settings, QString *errorMessage)
{
    static const QRegularExpression SegmentPattern(QStringLiteral(R"(^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}$)"));
    static const QRegularExpression RoutePattern(QStringLiteral(R"(^[A-Za-z0-9][A-Za-z0-9._:-]{0,126}(?:/[A-Za-z0-9][A-Za-z0-9._:-]{0,126})*$)"));
    static const QRegularExpression EnvironmentPattern(QStringLiteral(R"(^[A-Z_][A-Z0-9_]{1,127}$)"));
    if (!QSet<QString>{QStringLiteral("sync"), QStringLiteral("async"), QStringLiteral("cluster"), QStringLiteral("younglion"),
                       QStringLiteral("ddm")}
             .contains(settings.mode)) {
        fail(errorMessage, QStringLiteral("Choose a supported Python integration mode."));
        return {};
    }
    QString endpointError;
    if (!safeEndpoint(settings.endpoint, &endpointError)) {
        fail(errorMessage, endpointError);
        return {};
    }
    if (!SegmentPattern.match(settings.project).hasMatch() || !RoutePattern.match(settings.resource).hasMatch()
        || !SegmentPattern.match(settings.operation).hasMatch() || !EnvironmentPattern.match(settings.apiKeyEnvironment).hasMatch()) {
        fail(errorMessage, QStringLiteral("Project, route, operation or environment variable contains unsafe characters."));
        return {};
    }
    const auto endpoint = pythonEndpoint(settings.endpoint);
    const auto project = pythonLiteral(settings.project);
    const auto resource = pythonLiteral(settings.resource);
    const auto operation = pythonLiteral(settings.operation);
    const auto environment = pythonLiteral(settings.apiKeyEnvironment);
    if (settings.mode == QStringLiteral("sync")) {
        return QStringLiteral(
                   "import os\n\n"
                   "from json_api_forge import ForgeClient, RetryPolicy\n\n"
                   "retry = RetryPolicy(max_attempts=3, backoff_seconds=0.2, max_backoff_seconds=2.0)\n"
                   "with ForgeClient(%1, api_key=os.environ[%2], retry_policy=retry) as forge:\n"
                   "    health = forge.health().data\n"
                   "    records = list(forge.iter_items(%3, %4, page_size=100, max_items=10_000))\n"
                   "    summary = forge.call_operation(%3, %5, {}).data\n")
            .arg(endpoint, environment, project, resource, operation);
    }
    if (settings.mode == QStringLiteral("async")) {
        return QStringLiteral(
                   "import asyncio\n"
                   "import os\n\n"
                   "from json_api_forge import AsyncForgeClient, RetryPolicy\n\n"
                   "async def main() -> None:\n"
                   "    retry = RetryPolicy(max_attempts=3, backoff_seconds=0.2, max_backoff_seconds=2.0)\n"
                   "    async with AsyncForgeClient(%1, api_key=os.environ[%2], retry_policy=retry) as forge:\n"
                   "        health = (await forge.health()).data\n"
                   "        summary = (await forge.call_operation(%3, %4, {})).data\n"
                   "        print(health, summary)\n\n"
                   "asyncio.run(main())\n")
            .arg(endpoint, environment, project, operation);
    }
    if (settings.mode == QStringLiteral("cluster")) {
        QStringList endpointDefinitions;
        const auto rawEndpoints = settings.clusterEndpoints.split(u',', Qt::SkipEmptyParts);
        if (rawEndpoints.size() < 2 || rawEndpoints.size() > 16) {
            fail(errorMessage, QStringLiteral("Cluster mode requires 2–16 comma-separated endpoints."));
            return {};
        }
        int index = 0;
        for (const auto &raw : rawEndpoints) {
            const auto value = raw.trimmed();
            if (!safeEndpoint(value, &endpointError)) {
                fail(errorMessage, QStringLiteral("Cluster endpoint: %1").arg(endpointError));
                return {};
            }
            endpointDefinitions.append(
                QStringLiteral("    ForgeEndpoint(%1, %2, api_key=os.environ[%3]),")
                    .arg(pythonLiteral(QStringLiteral("region-%1").arg(++index)), pythonEndpoint(value), environment));
        }
        return QStringLiteral(
                   "import os\n\n"
                   "from json_api_forge import (\n"
                   "    CircuitBreakerPolicy, ForgeCluster, ForgeEndpoint, RoutingStrategy,\n"
                   ")\n\n"
                   "endpoints = [\n%1\n]\n"
                   "with ForgeCluster(\n"
                   "    endpoints,\n"
                   "    strategy=RoutingStrategy.RENDEZVOUS,\n"
                   "    circuit_breaker=CircuitBreakerPolicy(failure_threshold=3, recovery_seconds=15),\n"
                   ") as forge:\n"
                   "    summary = forge.call_operation(%2, %3, {}, routing_key=\"tenant-id\").data\n")
            .arg(endpointDefinitions.join(u'\n'), project, operation);
    }
    if (settings.mode == QStringLiteral("younglion")) {
        return QStringLiteral(
                   "import os\n\n"
                   "from YoungLion import DDM\n"
                   "from json_api_forge.integrations import YoungLionForgeClient\n\n"
                   "with YoungLionForgeClient.connect(%1, api_key=os.environ[%2]) as forge:\n"
                   "    payload = DDM({\"name\": \"integration-job\", \"status\": \"queued\"})\n"
                   "    created = forge.create_item(%3, %4, payload, idempotency_key=\"job-001\")\n"
                   "    print(created.data.to_dict())\n")
            .arg(endpoint, environment, project, resource);
    }
    return QStringLiteral(
               "import os\n\n"
               "from YoungLion import DDM\n"
               "from json_api_forge import ForgeClient\n"
               "from json_api_forge.integrations import DDMForgeClient\n\n"
               "raw = ForgeClient(%1, api_key=os.environ[%2])\n"
               "with DDMForgeClient(raw) as forge:\n"
               "    response = forge.call_operation(%3, %4, DDM({\"scope\": \"all\"}))\n"
               "    print(response.data.to_dict())\n")
        .arg(endpoint, environment, project, operation);
}

void PythonSdkPanel::refreshSnippet()
{
    QString error;
    const auto snippet = generatedSnippet(settings(), &error);
    m_clusterEndpoints->setEnabled(m_mode->currentData().toString() == QStringLiteral("cluster"));
    const auto extra = m_mode->currentData().toString() == QStringLiteral("younglion")
        ? QStringLiteral("younglion")
        : (m_mode->currentData().toString() == QStringLiteral("ddm") ? QStringLiteral("ddm") : QString());
    m_install->setText(extra.isEmpty() ? QStringLiteral("Install:  pip install json-api-forge-client")
                                       : QStringLiteral("Install:  pip install \"json-api-forge-client[%1]\"").arg(extra));
    if (snippet.isEmpty()) {
        m_snippet->setPlainText(QStringLiteral("# %1").arg(error));
        return;
    }
    m_snippet->setPlainText(snippet);
}

void PythonSdkPanel::copySnippet()
{
    QApplication::clipboard()->setText(m_snippet->toPlainText());
    emit statusMessage(QStringLiteral("Python integration snippet copied."));
}

void PythonSdkPanel::checkPackage()
{
    startPython(QStringLiteral("import json_api_forge; print('json-api-forge', json_api_forge.__version__)"),
                QStringLiteral("SDK package check"));
}

void PythonSdkPanel::runHealthCheck()
{
    const auto value = settings();
    QString error;
    if (!safeEndpoint(value.endpoint, &error)) {
        m_result->setText(error);
        return;
    }
    static const QRegularExpression EnvironmentPattern(QStringLiteral(R"(^[A-Z_][A-Z0-9_]{1,127}$)"));
    if (!EnvironmentPattern.match(value.apiKeyEnvironment).hasMatch()) {
        m_result->setText(QStringLiteral("The API key environment variable name is invalid."));
        return;
    }
    const auto script = QStringLiteral(
                            "import os\n"
                            "from json_api_forge import ForgeClient\n"
                            "key = os.environ.get(%1)\n"
                            "assert key, 'API key environment variable is not set'\n"
                            "with ForgeClient(%2, api_key=key, timeout=5.0) as client:\n"
                            "    response = client.health()\n"
                            "    print('HTTP', response.status_code, response.data)\n")
                            .arg(pythonLiteral(value.apiKeyEnvironment), pythonEndpoint(value.endpoint));
    startPython(script, QStringLiteral("Forge SDK health check"));
}

bool PythonSdkPanel::startPython(const QString &script, const QString &operation)
{
    if (m_process != nullptr) {
        m_result->setText(QStringLiteral("Another Python SDK check is already running."));
        return false;
    }
    auto executable = QStandardPaths::findExecutable(QStringLiteral("python3"));
    if (executable.isEmpty()) {
        executable = QStandardPaths::findExecutable(QStringLiteral("python"));
    }
    if (executable.isEmpty()) {
        m_result->setText(QStringLiteral("No Python interpreter was found on PATH."));
        return false;
    }
    auto *process = new QProcess(this);
    m_process = process;
    m_processOperation = operation;
    m_process->setProcessChannelMode(QProcess::MergedChannels);
    connect(process, &QProcess::errorOccurred, this, [this, process](QProcess::ProcessError error) {
        if (error != QProcess::FailedToStart || m_process != process) { return; }
        const auto message = QStringLiteral("%1 could not start: %2").arg(m_processOperation, process->errorString());
        m_result->setText(message);
        m_process = nullptr;
        process->deleteLater();
        emit statusMessage(message);
    });
    connect(process, &QProcess::finished, this, [this, process](int exitCode, QProcess::ExitStatus status) {
        if (m_process != process) { return; }
        const auto output = QString::fromUtf8(process->readAll()).trimmed().left(4096);
        const bool success = status == QProcess::NormalExit && exitCode == 0;
        m_result->setText(QStringLiteral("%1: %2\n%3")
                              .arg(m_processOperation, success ? QStringLiteral("passed") : QStringLiteral("failed"),
                                   output.isEmpty() ? QStringLiteral("No output") : output));
        emit statusMessage(QStringLiteral("%1 %2.").arg(m_processOperation, success ? QStringLiteral("passed") : QStringLiteral("failed")));
        m_process->deleteLater();
        m_process = nullptr;
    });
    m_result->setText(QStringLiteral("%1 is running…").arg(operation));
    m_process->start(executable, {QStringLiteral("-I"), QStringLiteral("-c"), script}, QIODevice::ReadOnly);
    QTimer::singleShot(10'000, m_process, [this] {
        if (m_process != nullptr && m_process->state() != QProcess::NotRunning) {
            m_process->kill();
        }
    });
    return true;
}
