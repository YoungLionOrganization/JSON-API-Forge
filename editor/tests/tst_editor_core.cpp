#include "ApiClient.hpp"
#include "ConnectionDialog.hpp"
#include "DocumentCodec.hpp"
#include "EditorSettings.hpp"
#include "GraphModel.hpp"
#include "NodeGraphEditor.hpp"
#include "PluginManager.hpp"
#include "PluginCatalogClient.hpp"
#include "PythonSdkPanel.hpp"
#include "TemplateManager.hpp"

#include <QCoreApplication>
#include <QComboBox>
#include <QCryptographicHash>
#include <QDir>
#include <QElapsedTimer>
#include <QEventLoop>
#include <QFile>
#include <QGraphicsView>
#include <QGraphicsScene>
#include <QGraphicsItem>
#include <limits>
#include <QHostAddress>
#include <QImage>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMouseEvent>
#include <QLabel>
#include <QProcess>
#include <QPushButton>
#include <QScopeGuard>
#include <QScrollBar>
#include <QSettings>
#include <QSharedPointer>
#include <QSignalSpy>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTemporaryDir>
#include <QTest>
#include <QTimer>
#include <QUrlQuery>
#include <QtEndian>

#include "ZipFixtureWriter.hpp"
#include "SafeZipReader.hpp"

#include <algorithm>
#include <functional>

class EditorCoreTests final : public QObject {
    Q_OBJECT

private slots:
    void jsonObjectsOnly();
    void documentPathPolicy();
    void atomicSaveAndDigest();
    void serverUrlPolicy();
    void tokenPolicy();
    void remoteDocumentRoutePolicy();
    void retryAndMutationPolicy();
    void founderReconciliationPolicy();
    void errorClassificationPolicy();
    void editorPreferenceBounds();
    void graphRightDragPanPolicy();
    void graphWireSceneRebuildPolicy();
    void accountSessionTransportPolicy();
    void callTicketUrlPolicy();
    void attachmentSnapshotPolicy();
    void brandAssetAndTheme();
    void pluginManifestPathPolicy();
    void pluginZipImportPolicy();
    void boundedZipDecoderPolicy();
    void cancellationStopsQueuedRetries();
    void graphModelPolicyAndCompiler();
    void graphCycleRollback();
    void graphCompilerRejectsDesignOnlyNodes();
    void pythonSdkSnippetPolicy();
    void pythonSdkFailedStartAllowsRetry();
    void pythonSdkInstallPackageIdentity();
    void forgePluginCatalogPolicy();
    void embeddedProjectTemplates();
};

namespace {
bool waitUntil(const std::function<bool()> &condition, int timeoutMs = 5000)
{
    QElapsedTimer timer;
    timer.start();
    while (!condition() && timer.elapsed() < timeoutMs) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 20);
        QTest::qWait(10);
    }
    return condition();
}

class LocalJsonServer final : public QTcpServer {
public:
    struct Response {
        QByteArray status;
        QByteArray body;
        QList<QPair<QByteArray, QByteArray>> headers;
        int delayMs = 0;
    };

    explicit LocalJsonServer(QObject *parent = nullptr)
        : QTcpServer(parent)
    {
        connect(this, &QTcpServer::newConnection, this, [this] {
            while (hasPendingConnections()) {
                auto *socket = nextPendingConnection();
                const auto bytes = QSharedPointer<QByteArray>::create();
                connect(socket, &QTcpSocket::readyRead, this, [this, socket, bytes] {
                    bytes->append(socket->readAll());
                    const auto headerEnd = bytes->indexOf("\r\n\r\n");
                    if (headerEnd < 0) {
                        return;
                    }
                    qint64 contentLength = 0;
                    for (const auto &line : bytes->left(headerEnd).split('\n')) {
                        const auto normalized = line.trimmed();
                        if (normalized.toLower().startsWith("content-length:")) {
                            contentLength = normalized.mid(15).trimmed().toLongLong();
                        }
                    }
                    const auto total = static_cast<qint64>(headerEnd) + 4 + contentLength;
                    if (bytes->size() < total) {
                        return;
                    }
                    requests.append(bytes->left(total));
                    const auto response = responses.isEmpty()
                        ? Response{QByteArray("500 Internal Server Error"), QByteArray("{}"), {}, 0}
                        : responses.takeFirst();
                    QByteArray output = QByteArray("HTTP/1.1 ") + response.status + QByteArray("\r\n")
                        + QByteArray("Content-Type: application/json\r\nConnection: close\r\nContent-Length: ")
                        + QByteArray::number(response.body.size()) + QByteArray("\r\n");
                    for (const auto &[name, value] : response.headers) {
                        output += name + QByteArray(": ") + value + QByteArray("\r\n");
                    }
                    output += QByteArray("\r\n") + response.body;
                    const auto deliver = [socket, output] {
                        socket->write(output);
                        socket->disconnectFromHost();
                    };
                    if (response.delayMs > 0) {
                        QTimer::singleShot(response.delayMs, socket, deliver);
                    } else {
                        deliver();
                    }
                });
            }
        });
    }

    void enqueue(const QByteArray &status, const QByteArray &body,
                 const QList<QPair<QByteArray, QByteArray>> &headers = {}, int delayMs = 0)
    {
        responses.append(Response{status, body, headers, delayMs});
    }

    QList<QByteArray> requests;
    QList<Response> responses;
};
} // namespace

void EditorCoreTests::jsonObjectsOnly()
{
    QJsonObject object;
    QString error;
    QVERIFY(DocumentCodec::parseObject(QByteArray(R"({"name":"Forge","enabled":true})"), &object, &error));
    QCOMPARE(object.value(QStringLiteral("name")).toString(), QStringLiteral("Forge"));
    QVERIFY(!DocumentCodec::parseObject(QByteArray(R"([1,2,3])"), &object, &error));
    QVERIFY(error.contains(QStringLiteral("root")));
    QVERIFY(!DocumentCodec::parseObject(QByteArray("{"), &object, &error));
}

void EditorCoreTests::documentPathPolicy()
{
    QVERIFY(DocumentCodec::isSafeDocumentPath(QStringLiteral("app.json"), false));
    QVERIFY(DocumentCodec::isSafeDocumentPath(QStringLiteral("config/40-resources.json"), false));
    QVERIFY(!DocumentCodec::isSafeDocumentPath(QStringLiteral("hooks/business.py"), false));
    QVERIFY(DocumentCodec::isSafeDocumentPath(QStringLiteral("hooks/business.py"), true));
    QVERIFY(!DocumentCodec::isSafeDocumentPath(QStringLiteral("../.env"), true));
    QVERIFY(!DocumentCodec::isSafeDocumentPath(QStringLiteral("config/nested/value.json"), true));
    QVERIFY(!DocumentCodec::isSafeDocumentPath(QStringLiteral("config\\value.json"), true));
    QVERIFY(DocumentCodec::isSafeDocumentPath(QStringLiteral("graphs/order-flow.forgegraph.json"), true));
    QVERIFY(!DocumentCodec::isSafeDocumentPath(QStringLiteral("graphs/OrderFlow.forgegraph.json"), true));
    QVERIFY(!DocumentCodec::isSafeDocumentPath(QStringLiteral("graphs/nested/order.forgegraph.json"), true));
}

void EditorCoreTests::atomicSaveAndDigest()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const auto path = directory.filePath(QStringLiteral("document.json"));
    const QByteArray content(R"({"resources":[]})");
    QString error;
    QVERIFY2(DocumentCodec::saveAtomically(path, content, &error), qPrintable(error));
    QFile file(path);
    QVERIFY(file.open(QIODevice::ReadOnly));
    QCOMPARE(file.readAll(), content);
    QCOMPARE(DocumentCodec::sha256(content).size(), 64);
}

void EditorCoreTests::serverUrlPolicy()
{
    QUrl normalized;
    QString error;
    QVERIFY(ApiClient::normalizeServerUrl(QUrl(QStringLiteral("https://forge.example.com/base/")), false, &normalized, &error));
    QCOMPARE(normalized.toString(), QStringLiteral("https://forge.example.com/base"));
    QVERIFY(!ApiClient::normalizeServerUrl(QUrl(QStringLiteral("http://forge.example.com")), false, &normalized, &error));
    QVERIFY(!ApiClient::normalizeServerUrl(QUrl(QStringLiteral("http://forge.example.com")), true, &normalized, &error));
    QVERIFY(ApiClient::normalizeServerUrl(QUrl(QStringLiteral("http://127.0.0.1:8000")), true, &normalized, &error));
    QVERIFY(ApiClient::normalizeServerUrl(QUrl(QStringLiteral("http://[::1]:8000")), true, &normalized, &error));
    QVERIFY(!ApiClient::normalizeServerUrl(QUrl(QStringLiteral("https://user:secret@forge.example.com")), false, &normalized, &error));
    QVERIFY(!ApiClient::normalizeServerUrl(QUrl(QStringLiteral("https://forge.example.com?token=x")), false, &normalized, &error));
    QVERIFY(!ApiClient::normalizeServerUrl(QUrl(QStringLiteral("https://forge.example.com/base/../admin")), false, &normalized, &error));
}

void EditorCoreTests::tokenPolicy()
{
    ApiClient client;
    QString error;
    QVERIFY(!client.configure(QUrl(QStringLiteral("https://forge.example.com")), QByteArray("short"), false, &error));
    QVERIFY(client.configure(QUrl(QStringLiteral("https://forge.example.com")),
                             QByteArray("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN"), false,
                             &error));
    QVERIFY(client.isConfigured());
    client.clearCredentials();
    QVERIFY(!client.isConfigured());
    QVERIFY(!client.configure(QUrl(QStringLiteral("https://forge.example.com")),
                              QByteArray("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN\n"),
                              false, &error));

    LocalJsonServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    const QByteArray sessionToken("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN");
    const auto authResponse = QJsonDocument(
        QJsonObject{{QStringLiteral("access_token"), QString::fromUtf8(sessionToken)},
                    {QStringLiteral("profile"), QJsonObject{{QStringLiteral("username"), QStringLiteral("worker")}}}})
                                  .toJson(QJsonDocument::Compact);
    server.enqueue(QByteArray("201 Created"), authResponse);
    server.enqueue(QByteArray("201 Created"), authResponse);
    const auto endpoint = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QVERIFY(client.configureServer(endpoint, true, &error));
    QSignalSpy received(&client, &ApiClient::jsonReceived);
    QSignalSpy failed(&client, &ApiClient::requestFailed);

    const auto invitation = QStringLiteral("jfi_") + QString(40, u'a');
    client.registerMember(QStringLiteral("  ") + invitation + QStringLiteral("\r\n"),
                          QStringLiteral("worker.one"), QStringLiteral("a secure password"),
                          QStringLiteral("Worker One"));
    QVERIFY(waitUntil([&received] { return received.size() == 1; }));
    QCOMPARE(failed.size(), 0);
    const auto registerBody = server.requests.at(0).mid(server.requests.at(0).indexOf("\r\n\r\n") + 4);
    QCOMPARE(QJsonDocument::fromJson(registerBody).object().value(QStringLiteral("invitation")).toString(),
             invitation);

    QVERIFY(client.configureServer(endpoint, true, &error));
    client.setupFounder(QByteArray(" \r\n") + QByteArray(32, 'a') + QByteArray("\n\t"),
                        QStringLiteral("founder"),
                        QStringLiteral("a secure password"), QStringLiteral("Founder"));
    QVERIFY(waitUntil([&received] { return received.size() == 2; }));
    QCOMPARE(failed.size(), 0);
    QVERIFY(server.requests.at(1).toLower().contains(
        QByteArray("x-forge-setup-token: ") + QByteArray(32, 'a') + QByteArray("\r\n")));

    client.setupFounder(QByteArray(16, 'a') + QByteArray(" ") + QByteArray(16, 'b'),
                        QStringLiteral("founder"), QStringLiteral("a secure password"),
                        QStringLiteral("Founder"));
    QCOMPARE(failed.size(), 1);
    client.registerMember(QStringLiteral("jfi_") + QString(20, u'a') + u' ' + QString(20, u'b'),
                          QStringLiteral("worker.one"), QStringLiteral("a secure password"),
                          QStringLiteral("Worker One"));
    QCOMPARE(failed.size(), 2);
}

void EditorCoreTests::remoteDocumentRoutePolicy()
{
    QCOMPARE(ApiClient::documentPathSegments(QStringLiteral("app.json")),
             QStringList{QStringLiteral("app.json")});
    QCOMPARE(ApiClient::documentPathSegments(QStringLiteral("config/40-resources.json")),
             (QStringList{QStringLiteral("config"), QStringLiteral("40-resources.json")}));
    QVERIFY(ApiClient::documentPathSegments(QStringLiteral("../app.json")).isEmpty());
    QVERIFY(ApiClient::documentPathSegments(QStringLiteral("config/nested/value.json")).isEmpty());
    QVERIFY(ApiClient::documentPathSegments(QStringLiteral("config%2Fsecret.json")).isEmpty());
    QVERIFY(ApiClient::documentPathSegments(QStringLiteral("config/%2e%2e.json")).isEmpty());
    QVERIFY(ApiClient::documentPathSegments(QStringLiteral("hooks/a%5Cb.py")).isEmpty());

    LocalJsonServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    const QStringList paths{QStringLiteral("config/40-resources.json"),
                            QStringLiteral("hooks/business.py"),
                            QStringLiteral("graphs/order-flow.forgegraph.json")};
    for (const auto &path : paths) {
        server.enqueue(QByteArray("200 OK"),
                       QJsonDocument(QJsonObject{{QStringLiteral("path"), path},
                                                 {QStringLiteral("content"), QStringLiteral("{}")},
                                                 {QStringLiteral("sha256"), QString(64, u'a')}})
                           .toJson(QJsonDocument::Compact));
    }
    ApiClient client;
    QString error;
    const auto endpoint = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QVERIFY(client.configure(endpoint,
                             QByteArray("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN"),
                             true, &error));
    QSignalSpy received(&client, &ApiClient::jsonReceived);
    QSignalSpy failed(&client, &ApiClient::requestFailed);
    for (const auto &path : paths) {
        client.fetchDocument(QStringLiteral("DemoProject"), path);
    }
    QVERIFY(waitUntil([&received] { return received.size() == 3; }));
    QCOMPARE(server.requests.size(), 3);
    // Requests are concurrent; their arrival order varies between Qt/platforms.
    // Compare complete request lines, preserving duplicate/missing-route checks.
    QList<QByteArray> actualRoutes;
    QList<QByteArray> expectedRoutes;
    for (const auto &request : server.requests) {
        actualRoutes.append(request.left(request.indexOf("\r\n")));
    }
    for (const auto &path : paths) {
        expectedRoutes.append(QByteArray("GET /__forge/editor/v1/projects/DemoProject/documents/")
                              + path.toUtf8() + QByteArray(" HTTP/1.1"));
    }
    std::sort(actualRoutes.begin(), actualRoutes.end());
    std::sort(expectedRoutes.begin(), expectedRoutes.end());
    QCOMPARE(actualRoutes, expectedRoutes);

    client.fetchDocument(QStringLiteral("DemoProject"), QStringLiteral("config/../secret.json"));
    client.fetchDocument(QStringLiteral("DemoProject"), QStringLiteral("config%2Fsecret.json"));
    QCOMPARE(failed.size(), 2);
    QCOMPARE(server.requests.size(), 3);
}

void EditorCoreTests::retryAndMutationPolicy()
{
    LocalJsonServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    server.enqueue(QByteArray("503 Service Unavailable"), QByteArray(R"({"detail":"busy"})"));
    server.enqueue(QByteArray("200 OK"), QByteArray(R"({"projects":[]})"));
    server.enqueue(QByteArray("503 Service Unavailable"), QByteArray(R"({"detail":"save unavailable"})"));
    server.enqueue(QByteArray("200 OK"), QByteArray(R"({"path":"app.json","sha256":"x"})"), {}, 1500);

    ApiClient client;
    EditorPreferences preferences;
    preferences.requestTimeoutMs = 1000;
    preferences.safeGetRetries = 1;
    preferences.retryBaseDelayMs = 100;
    client.applyPreferences(preferences);
    QString error;
    const auto endpoint = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QVERIFY(client.configure(endpoint,
                             QByteArray("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN"),
                             true, &error));
    QSignalSpy received(&client, &ApiClient::jsonReceived);
    QSignalSpy detailed(&client, &ApiClient::requestFailedDetailed);

    client.fetchProjects();
    QVERIFY(waitUntil([&received] { return received.size() == 1; }));
    QCOMPARE(server.requests.size(), 2);

    client.saveDocument(QStringLiteral("DemoProject"), QStringLiteral("app.json"),
                        QByteArray("{}"), QString(64, u'a'));
    QVERIFY(waitUntil([&detailed] { return detailed.size() == 1; }));
    QCOMPARE(server.requests.size(), 3);
    QCOMPARE(detailed.at(0).at(5).toBool(), false);

    client.saveDocument(QStringLiteral("DemoProject"), QStringLiteral("app.json"),
                        QByteArray("{\"changed\":true}"), QString(64, u'a'));
    QVERIFY(waitUntil([&detailed] { return detailed.size() == 2; }, 2500));
    QCOMPARE(server.requests.size(), 4);
    QCOMPARE(detailed.at(1).at(3).toString(), QStringLiteral("timeout"));
    QCOMPARE(detailed.at(1).at(5).toBool(), true);
    QTest::qWait(250);
    QCOMPARE(server.requests.size(), 4);
}

void EditorCoreTests::founderReconciliationPolicy()
{
    LocalJsonServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    server.enqueue(QByteArray("201 Created"),
                   QByteArray(R"({"access_token":"jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN"})"),
                   {}, 1500);
    server.enqueue(QByteArray("200 OK"), QByteArray(R"({"initialized":true})"));
    server.enqueue(QByteArray("409 Conflict"), QByteArray(R"({"detail":"already configured"})"));
    server.enqueue(QByteArray("200 OK"), QByteArray(R"({"initialized":true})"));

    ApiClient client;
    EditorPreferences preferences;
    preferences.authenticationTimeoutMs = 1000;
    preferences.safeGetRetries = 0;
    client.applyPreferences(preferences);
    QString error;
    const auto endpoint = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QVERIFY2(client.configureServer(endpoint, true, &error), qPrintable(error));
    QSignalSpy received(&client, &ApiClient::jsonReceived);
    QSignalSpy detailed(&client, &ApiClient::requestFailedDetailed);

    const QByteArray setupToken(40, 's');
    client.setupFounder(QByteArray("\r\n  ") + setupToken + QByteArray("\t\n"),
                        QStringLiteral("founder"), QStringLiteral("correct horse battery staple"),
                        QStringLiteral("Forge Founder"));
    QVERIFY(waitUntil([&detailed] { return detailed.size() == 1; }, 2500));
    QCOMPARE(server.requests.size(), 1);
    QCOMPARE(detailed.at(0).at(0).toString(), QStringLiteral("auth-setup"));
    QCOMPARE(detailed.at(0).at(3).toString(), QStringLiteral("timeout"));
    QCOMPARE(detailed.at(0).at(5).toBool(), true);
    QVERIFY(!server.requests.at(0).contains("\r\n  "));
    QVERIFY(server.requests.at(0).toLower().contains(
        QByteArray("x-forge-setup-token: ") + setupToken));

    client.fetchSetupStatus(QStringLiteral("setup-reconcile-timeout"));
    QVERIFY(waitUntil([&received] { return received.size() == 1; }));
    QCOMPARE(received.at(0).at(0).toString(), QStringLiteral("setup-reconcile-timeout"));
    QCOMPARE(received.at(0).at(1).toJsonObject().value(QStringLiteral("initialized")).toBool(), true);

    client.setupFounder(setupToken, QStringLiteral("founder"),
                        QStringLiteral("correct horse battery staple"), QStringLiteral("Forge Founder"));
    QVERIFY(waitUntil([&detailed] { return detailed.size() == 2; }));
    QCOMPARE(server.requests.size(), 3);
    QCOMPARE(detailed.at(1).at(3).toString(), QStringLiteral("conflict"));
    QCOMPARE(detailed.at(1).at(5).toBool(), false);

    client.fetchSetupStatus(QStringLiteral("setup-reconcile-conflict"));
    QVERIFY(waitUntil([&received] { return received.size() == 2; }));
    QCOMPARE(received.at(1).at(0).toString(), QStringLiteral("setup-reconcile-conflict"));
    QCOMPARE(received.at(1).at(1).toJsonObject().value(QStringLiteral("initialized")).toBool(), true);

    ConnectionDialog dialog(endpoint);
    dialog.setInitialMode(ConnectionDialog::AuthenticationMode::FounderSetup,
                          QStringLiteral("founder"));
    QCOMPARE(dialog.authenticationMode(), ConnectionDialog::AuthenticationMode::FounderSetup);
    QCOMPARE(dialog.username(), QStringLiteral("founder"));
    dialog.setInitialMode(ConnectionDialog::AuthenticationMode::SignIn,
                          QStringLiteral("founder"));
    QCOMPARE(dialog.authenticationMode(), ConnectionDialog::AuthenticationMode::SignIn);
    QCOMPARE(dialog.username(), QStringLiteral("founder"));
    QVERIFY(dialog.password().isEmpty());
}

void EditorCoreTests::errorClassificationPolicy()
{
    LocalJsonServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    server.enqueue(QByteArray("403 Forbidden"), QByteArray(R"({"detail":"IP policy denied the request"})"));
    server.enqueue(QByteArray("409 Conflict"), QByteArray(R"({"detail":"stale revision"})"));
    server.enqueue(QByteArray("422 Unprocessable Entity"), QByteArray(R"({"detail":"invalid payload"})"));
    server.enqueue(QByteArray("429 Too Many Requests"), QByteArray(R"({"detail":"slow down"})"));
    server.enqueue(QByteArray("503 Service Unavailable"), QByteArray(R"({"detail":"maintenance"})"));
    server.enqueue(QByteArray("200 OK"), QByteArray("not-json"));
    server.enqueue(QByteArray("200 OK"), QByteArray(1024 * 1024 + 1, 'x'));
    server.enqueue(QByteArray("401 Unauthorized"), QByteArray(R"({"detail":"expired"})"));
    server.enqueue(QByteArray("200 OK"), QByteArray(R"({"projects":[]})"), {}, 1500);

    ApiClient client;
    EditorPreferences preferences;
    preferences.requestTimeoutMs = 3000;
    preferences.safeGetRetries = 0;
    preferences.maxResponseMiB = 1;
    client.applyPreferences(preferences);
    const QByteArray token("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN");
    QString error;
    const auto endpoint = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QVERIFY2(client.configure(endpoint, token, true, &error), qPrintable(error));
    QSignalSpy detailed(&client, &ApiClient::requestFailedDetailed);

    const QStringList expectedCategories{
        QStringLiteral("authorization"), QStringLiteral("conflict"),
        QStringLiteral("validation"), QStringLiteral("rate-limit"),
        QStringLiteral("server"), QStringLiteral("response"),
        QStringLiteral("response"), QStringLiteral("authentication")};
    for (qsizetype index = 0; index < expectedCategories.size(); ++index) {
        client.fetchProjects();
        const auto expectedFailures = static_cast<int>(index + 1);
        QVERIFY(waitUntil([&detailed, expectedFailures] { return detailed.size() == expectedFailures; }));
        QCOMPARE(detailed.at(index).at(3).toString(), expectedCategories.at(index));
        QVERIFY(!detailed.at(index).at(4).toString().contains(QString::fromUtf8(token)));
    }
    QVERIFY(!client.isConfigured());

    QVERIFY2(client.configure(endpoint, token, true, &error), qPrintable(error));
    client.fetchProjects();
    QVERIFY(waitUntil([&server] { return server.requests.size() == 9; }));
    client.cancelActiveRequests();
    QVERIFY(waitUntil([&detailed] { return detailed.size() == 9; }));
    QCOMPARE(detailed.at(8).at(3).toString(), QStringLiteral("canceled"));
    QVERIFY(detailed.at(8).at(2).toString().contains(QStringLiteral("user"), Qt::CaseInsensitive));

    QTcpServer portReservation;
    QVERIFY(portReservation.listen(QHostAddress::LocalHost));
    const auto closedPort = portReservation.serverPort();
    portReservation.close();
    QVERIFY2(client.configureServer(QUrl(QStringLiteral("http://127.0.0.1:%1").arg(closedPort)), true,
                                    &error), qPrintable(error));
    client.fetchSetupStatus(QStringLiteral("closed-port"));
    QVERIFY(waitUntil([&detailed] { return detailed.size() == 10; }));
    QCOMPARE(detailed.at(9).at(3).toString(), QStringLiteral("connection-refused"));
}

void EditorCoreTests::editorPreferenceBounds()
{
    EditorPreferences preferences;
    preferences.requestTimeoutMs = -1;
    preferences.authenticationTimeoutMs = 5'000'000;
    preferences.uploadTimeoutMs = 0;
    preferences.downloadTimeoutMs = 5'000'000;
    preferences.safeGetRetries = 99;
    preferences.retryBaseDelayMs = 1;
    preferences.maxResponseMiB = 999;
    preferences.graphZoomSensitivity = 99.0;
    preferences.clamp();
    QCOMPARE(preferences.requestTimeoutMs, 1000);
    QCOMPARE(preferences.authenticationTimeoutMs, 900'000);
    QCOMPARE(preferences.uploadTimeoutMs, 1000);
    QCOMPARE(preferences.downloadTimeoutMs, 900'000);
    QCOMPARE(preferences.safeGetRetries, 3);
    QCOMPARE(preferences.retryBaseDelayMs, 100);
    QCOMPARE(preferences.maxResponseMiB, 64);
    QCOMPARE(preferences.graphZoomSensitivity, 3.0);
    preferences.graphZoomSensitivity = std::numeric_limits<double>::quiet_NaN();
    preferences.clamp();
    QCOMPARE(preferences.graphZoomSensitivity, 1.0);

    QTemporaryDir settingsDirectory;
    QVERIFY(settingsDirectory.isValid());
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, settingsDirectory.path());
    QCoreApplication::setOrganizationName(QStringLiteral("ForgeEditorTests"));
    QCoreApplication::setApplicationName(QStringLiteral("PreferencePersistence"));
    QSettings().clear();
    preferences.requestTimeoutMs = 75'000;
    preferences.authenticationTimeoutMs = 140'000;
    preferences.uploadTimeoutMs = 210'000;
    preferences.downloadTimeoutMs = 220'000;
    preferences.safeGetRetries = 2;
    preferences.retryBaseDelayMs = 900;
    preferences.maxResponseMiB = 12;
    preferences.confirmDiscard = false;
    preferences.restoreWindowLayout = false;
    preferences.rightDragPan = false;
    preferences.graphZoomSensitivity = 1.4;
    preferences.serverHistory = {QStringLiteral("https://forge.example.com"),
                                 QStringLiteral("https://user:secret@invalid.example.com")};
    preferences.save();
    const auto loaded = EditorPreferences::load();
    QCOMPARE(loaded.requestTimeoutMs, 75'000);
    QCOMPARE(loaded.authenticationTimeoutMs, 140'000);
    QCOMPARE(loaded.uploadTimeoutMs, 210'000);
    QCOMPARE(loaded.downloadTimeoutMs, 220'000);
    QCOMPARE(loaded.safeGetRetries, 2);
    QCOMPARE(loaded.retryBaseDelayMs, 900);
    QCOMPARE(loaded.maxResponseMiB, 12);
    QCOMPARE(loaded.confirmDiscard, false);
    QCOMPARE(loaded.restoreWindowLayout, false);
    QCOMPARE(loaded.rightDragPan, false);
    QCOMPARE(loaded.graphZoomSensitivity, 1.4);
    QCOMPARE(loaded.serverHistory, QStringList{QStringLiteral("https://forge.example.com")});
}

void EditorCoreTests::graphRightDragPanPolicy()
{
    NodeGraphEditor editor;
    editor.resize(1000, 650);
    editor.setInteractionSettings(true, 1.0);
    QString error;
    QVERIFY(editor.setDocument(
        NodeGraphEditor::starterDocument(QStringLiteral("config/50-operation.json")), &error));
    editor.show();
    QCoreApplication::processEvents();
    auto *view = editor.findChild<QGraphicsView *>(QStringLiteral("graphCanvas"));
    QVERIFY(view != nullptr);
    view->centerOn(QPointF(0.0, 0.0));
    QCoreApplication::processEvents();
    const auto beforeDocument = editor.document();
    const auto beforeTransform = view->transform();
    const int beforeHorizontal = view->horizontalScrollBar()->value();
    const int beforeVertical = view->verticalScrollBar()->value();
    const QPoint start(view->viewport()->width() / 2, view->viewport()->height() / 2);
    const QPoint first = start + QPoint(60, 40);
    const QPoint second = start + QPoint(130, 90);
    const auto globalStart = view->viewport()->mapToGlobal(start);
    const auto globalFirst = view->viewport()->mapToGlobal(first);
    const auto globalSecond = view->viewport()->mapToGlobal(second);
    QMouseEvent press(QEvent::MouseButtonPress, QPointF(start), QPointF(start),
                      QPointF(globalStart), Qt::RightButton, Qt::RightButton, Qt::NoModifier);
    QApplication::sendEvent(view->viewport(), &press);
    QMouseEvent moveOne(QEvent::MouseMove, QPointF(first), QPointF(first), QPointF(globalFirst),
                        Qt::NoButton, Qt::RightButton, Qt::NoModifier);
    QApplication::sendEvent(view->viewport(), &moveOne);
    QMouseEvent moveTwo(QEvent::MouseMove, QPointF(second), QPointF(second), QPointF(globalSecond),
                        Qt::NoButton, Qt::RightButton, Qt::NoModifier);
    QApplication::sendEvent(view->viewport(), &moveTwo);
    QMouseEvent release(QEvent::MouseButtonRelease, QPointF(second), QPointF(second),
                        QPointF(globalSecond), Qt::RightButton, Qt::NoButton, Qt::NoModifier);
    QApplication::sendEvent(view->viewport(), &release);
    QCOMPARE(editor.document(), beforeDocument);
    QCOMPARE(view->transform(), beforeTransform);
    QVERIFY(view->horizontalScrollBar()->value() != beforeHorizontal
            || view->verticalScrollBar()->value() != beforeVertical);
}

void EditorCoreTests::graphWireSceneRebuildPolicy()
{
    NodeGraphEditor editor;
    editor.resize(1400, 700);
    auto document = NodeGraphEditor::starterDocument(QStringLiteral("config/50-operation.json"));
    const auto nodes = document.value(QStringLiteral("nodes")).toArray();
    document.insert(QStringLiteral("nodes"), QJsonArray{nodes.at(0), nodes.at(1)});
    document.insert(QStringLiteral("edges"), QJsonArray{});
    QString error;
    QVERIFY2(editor.setDocument(document, &error), qPrintable(error));
    editor.show();
    QCoreApplication::processEvents();
    auto *view = editor.findChild<QGraphicsView *>(QStringLiteral("graphCanvas"));
    QVERIFY(view != nullptr);
    view->resetTransform();
    view->centerOn(QPointF(260.0, 60.0));
    QCoreApplication::processEvents();
    const auto start = view->mapFromScene(QPointF(222.0, 76.0));
    const auto end = view->mapFromScene(QPointF(302.0, 76.0));
    QSignalSpy changes(&editor, &NodeGraphEditor::documentChanged);
    QTest::mousePress(view->viewport(), Qt::LeftButton, Qt::NoModifier, start);
    QTest::mouseRelease(view->viewport(), Qt::LeftButton, Qt::NoModifier, end);
    QCOMPARE(editor.document().value(QStringLiteral("edges")).toArray().size(), 1);
    QCOMPARE(changes.size(), 1);
    // Loading a document while a wire is dragged must also clear scene pointers.
    QTest::mousePress(view->viewport(), Qt::LeftButton, Qt::NoModifier, start);
    QVERIFY(editor.setDocument(document, &error));
    QTest::mouseRelease(view->viewport(), Qt::LeftButton, Qt::NoModifier, end);
    QCOMPARE(editor.document().value(QStringLiteral("edges")).toArray().size(), 0);
}

void EditorCoreTests::accountSessionTransportPolicy()
{
    LocalJsonServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    const QByteArray token("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN");
    server.enqueue(QByteArray("200 OK"),
                   QJsonDocument(QJsonObject{{QStringLiteral("access_token"), QString::fromUtf8(token)}})
                       .toJson(QJsonDocument::Compact),
                   {{QByteArray("Set-Cookie"), QByteArray("ambient=must-not-return; Path=/")}});
    server.enqueue(QByteArray("200 OK"),
                   QByteArray(R"({"username":"worker","display_name":"Worker"})"));
    server.enqueue(QByteArray("302 Found"), QByteArray("{}"),
                   {{QByteArray("Location"), QByteArray("http://127.0.0.1/credential-sink")}});
    server.enqueue(QByteArray("401 Unauthorized"), QByteArray(R"({"detail":"expired"})"));

    ApiClient client;
    QString error;
    const auto endpoint = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(server.serverPort()));
    QVERIFY2(client.configureServer(endpoint, true, &error), qPrintable(error));
    QSignalSpy received(&client, &ApiClient::jsonReceived);
    QSignalSpy failed(&client, &ApiClient::requestFailed);

    client.login(QStringLiteral("worker"), QStringLiteral("correct horse battery staple"));
    QVERIFY(waitUntil([&received] { return received.size() == 1; }));
    QVERIFY(client.isConfigured());
    client.fetchProfile();
    QVERIFY(waitUntil([&received] { return received.size() == 2; }));
    QCOMPARE(server.requests.size(), 2);
    const auto loginRequest = server.requests.at(0).toLower();
    const auto profileRequest = server.requests.at(1).toLower();
    QVERIFY(loginRequest.startsWith("post /__forge/editor/v1/auth/login http/1.1\r\n"));
    QVERIFY(!loginRequest.contains("authorization:"));
    QVERIFY(!loginRequest.contains("x-forge-editor-token:"));
    QVERIFY(loginRequest.contains("cache-control: no-store"));
    QVERIFY(profileRequest.startsWith("get /__forge/editor/v1/me http/1.1\r\n"));
    QVERIFY(profileRequest.contains(QByteArray("authorization: bearer ") + token.toLower()));
    QVERIFY(!profileRequest.contains("x-forge-editor-token:"));
    QVERIFY(!profileRequest.contains("cookie:"));

    client.fetchMembers();
    QVERIFY(waitUntil([&failed] { return failed.size() == 1; }));
    QCOMPARE(server.requests.size(), 3);
    QVERIFY(failed.at(0).at(2).toString().contains(QStringLiteral("Redirects")));
    QVERIFY(client.isConfigured());

    client.fetchMembers();
    QVERIFY(waitUntil([&failed] { return failed.size() == 2; }));
    QCOMPARE(server.requests.size(), 4);
    QVERIFY(!client.isConfigured());
}

void EditorCoreTests::callTicketUrlPolicy()
{
    ApiClient client;
    QString error;
    QVERIFY(client.configure(QUrl(QStringLiteral("https://forge.example.com/admin")),
                             QByteArray("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN"), false,
                             &error));
    const auto ticket = QStringLiteral("jfc_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK");
    const auto url = client.callClientUrl(QStringLiteral("/__forge/editor/v1/call-client/call-id"), ticket);
    QCOMPARE(url.host(), QStringLiteral("forge.example.com"));
    QCOMPARE(url.path(), QStringLiteral("/admin/__forge/editor/v1/call-client/call-id"));
    QVERIFY(url.query().isEmpty());
    QCOMPARE(QUrlQuery(url.fragment()).queryItemValue(QStringLiteral("ticket")),
             ticket);
    QVERIFY(!client.callClientUrl(QStringLiteral("/../redirect"), QStringLiteral("ticket")).isValid());
    QVERIFY(!client.callClientUrl(QStringLiteral("/__forge/editor/v1/call-client/../redirect"), ticket).isValid());
    QVERIFY(!client.callClientUrl(QStringLiteral("/__forge/editor/v1/call-client/call-id"), ticket + u'\n').isValid());
}

void EditorCoreTests::attachmentSnapshotPolicy()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    ApiClient client;
    QString error;
    QVERIFY(client.configure(QUrl(QStringLiteral("https://forge.example.com")),
                             QByteArray("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN"), false,
                             &error));
    QSignalSpy failed(&client, &ApiClient::requestFailed);

    client.uploadAttachment(QStringLiteral("area-1"), directory.path(), 1024);
    QCOMPARE(failed.size(), 1);

    const auto oversizedPath = directory.filePath(QStringLiteral("oversized.bin"));
    QFile oversized(oversizedPath);
    QVERIFY(oversized.open(QIODevice::WriteOnly));
    QCOMPARE(oversized.write(QByteArray(1025, 'x')), static_cast<qint64>(1025));
    oversized.close();
    client.uploadAttachment(QStringLiteral("area-1"), oversizedPath, 1024);
    QCOMPARE(failed.size(), 2);

    const auto linkPath = directory.filePath(QStringLiteral("attachment-link.bin"));
    if (QFile::link(oversizedPath, linkPath) && QFileInfo(linkPath).isSymLink()) {
        client.uploadAttachment(QStringLiteral("area-1"), linkPath, 4096);
        QCOMPARE(failed.size(), 3);
    }
}

void EditorCoreTests::brandAssetAndTheme()
{
    const QImage mark(QStringLiteral(":/branding/mark.png"));
    QVERIFY(!mark.isNull());
    QVERIFY(mark.hasAlphaChannel());
    QFile style(QStringLiteral(":/styles/dark.qss"));
    QVERIFY(style.open(QIODevice::ReadOnly));
    const auto qss = style.readAll();
    QVERIFY(qss.contains("#f2b84b"));
    QVERIFY(qss.contains("#202225"));
    QVERIFY(!qss.contains("#0c1016"));
}

void EditorCoreTests::pluginManifestPathPolicy()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    QDir root(directory.path());
    QVERIFY(root.mkpath(QStringLiteral("plugins")));
    QFile outsideLibrary(root.filePath(QStringLiteral("escape.so")));
    QVERIFY(outsideLibrary.open(QIODevice::WriteOnly));
    QVERIFY(outsideLibrary.write("not a plugin") > 0);
    outsideLibrary.close();

    QFile manifest(root.filePath(QStringLiteral("plugins/escape.forgeplugin.json")));
    QVERIFY(manifest.open(QIODevice::WriteOnly));
    const QJsonObject definition{
        {QStringLiteral("id"), QStringLiteral("vendor.escape")},
        {QStringLiteral("name"), QStringLiteral("Escape attempt")},
        {QStringLiteral("version"), QStringLiteral("1.0.0")},
        {QStringLiteral("apiVersion"), ForgeEditor::PluginApiVersion},
        {QStringLiteral("library"), QStringLiteral("../escape.so")},
        {QStringLiteral("sha256"), QString(64, u'0')},
    };
    QVERIFY(manifest.write(QJsonDocument(definition).toJson(QJsonDocument::Compact)) > 0);
    manifest.close();

    const PluginManager manager({root.filePath(QStringLiteral("plugins"))});
    const auto descriptors = manager.discover();
    QCOMPARE(descriptors.size(), 1);
    QVERIFY(!descriptors.first().error.isEmpty());
    QVERIFY(descriptors.first().libraryPath.isEmpty());
}

void EditorCoreTests::pluginZipImportPolicy()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const auto destination = QDir(directory.path()).filePath(QStringLiteral("installed"));
#ifdef Q_OS_WIN
    const auto libraryName = QStringLiteral("safeplugin.dll");
#elif defined(Q_OS_MACOS)
    const auto libraryName = QStringLiteral("libsafeplugin.dylib");
#else
    const auto libraryName = QStringLiteral("libsafeplugin.so");
#endif
    const QByteArray libraryBytes("fixture native library bytes");
    const auto libraryDigest = QString::fromLatin1(
        QCryptographicHash::hash(libraryBytes, QCryptographicHash::Sha256).toHex());
    const QJsonObject definition{
        {QStringLiteral("id"), QStringLiteral("example.safe")},
        {QStringLiteral("name"), QStringLiteral("Safe fixture")},
        {QStringLiteral("version"), QStringLiteral("0.5.1")},
        {QStringLiteral("apiVersion"), ForgeEditor::PluginApiVersion},
        {QStringLiteral("library"), libraryName},
        {QStringLiteral("sha256"), libraryDigest},
        {QStringLiteral("permissions"), QJsonArray{QStringLiteral("editor.palette")}},
    };
    const auto manifestBytes = QJsonDocument(definition).toJson(QJsonDocument::Compact);

    const auto safeArchive = QDir(directory.path()).filePath(QStringLiteral("safe.zip"));
    {
        ZipFixtureWriter writer(safeArchive);
        writer.setCompressionPolicy(ZipFixtureWriter::AlwaysCompress);
        writer.addDirectory(QStringLiteral("safe-package"));
        writer.addFile(QStringLiteral("safe-package/example.safe.forgeplugin.json"), manifestBytes);
        writer.addFile(QStringLiteral("safe-package/") + libraryName, libraryBytes);
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    QSettings settings;
    settings.setValue(QStringLiteral("plugins/enabledIds"), QStringList{});
    const auto imported = PluginManager::importZip(safeArchive, destination);
    QVERIFY2(imported.success, qPrintable(imported.error));
    QCOMPARE(imported.pluginId, QStringLiteral("example.safe"));
    QVERIFY(QFileInfo(imported.installDirectory).isDir());
    QCOMPARE(settings.value(QStringLiteral("plugins/enabledIds")).toStringList(), QStringList{});
    const PluginManager manager({destination});
    const auto descriptors = manager.discover();
    QCOMPARE(descriptors.size(), 1);
    QCOMPARE(descriptors.first().id, QStringLiteral("example.safe"));
    QVERIFY2(descriptors.first().error.isEmpty(), qPrintable(descriptors.first().error));

    const auto duplicate = PluginManager::importZip(safeArchive, destination);
    QVERIFY(!duplicate.success);
    QVERIFY(duplicate.error.contains(QStringLiteral("already installed")));

    const auto traversalArchive = QDir(directory.path()).filePath(QStringLiteral("traversal.zip"));
    {
        ZipFixtureWriter writer(traversalArchive);
        writer.addFile(QStringLiteral("../escaped.txt"), QByteArray("must not escape"));
        writer.addFile(QStringLiteral("example.safe.forgeplugin.json"), manifestBytes);
        writer.addFile(libraryName, libraryBytes);
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    const auto traversal = PluginManager::importZip(traversalArchive,
                                                     QDir(directory.path()).filePath(QStringLiteral("traversal-target")));
    QVERIFY(!traversal.success);
    QVERIFY(!QFileInfo::exists(QDir(directory.path()).filePath(QStringLiteral("escaped.txt"))));

    const auto linkArchive = QDir(directory.path()).filePath(QStringLiteral("link.zip"));
    {
        ZipFixtureWriter writer(linkArchive);
        writer.addFile(QStringLiteral("example.safe.forgeplugin.json"), manifestBytes);
        writer.addFile(libraryName, libraryBytes);
        writer.addSymLink(QStringLiteral("plugin-link"), QStringLiteral("../../outside"));
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    const auto linked = PluginManager::importZip(linkArchive,
                                                  QDir(directory.path()).filePath(QStringLiteral("link-target")));
    QVERIFY(!linked.success);
    QVERIFY(linked.error.contains(QStringLiteral("Symbolic links")));

    const auto collisionArchive = QDir(directory.path()).filePath(QStringLiteral("collision.zip"));
    {
        ZipFixtureWriter writer(collisionArchive);
        writer.addFile(QStringLiteral("example.safe.forgeplugin.json"), manifestBytes);
        writer.addFile(libraryName, libraryBytes);
        writer.addFile(libraryName.toUpper(), libraryBytes);
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    const auto collision = PluginManager::importZip(
        collisionArchive, QDir(directory.path()).filePath(QStringLiteral("collision-target")));
    QVERIFY(!collision.success);
    QVERIFY(collision.error.contains(QStringLiteral("case-colliding")));

    const auto multipleManifestArchive = QDir(directory.path()).filePath(QStringLiteral("multiple-manifests.zip"));
    {
        ZipFixtureWriter writer(multipleManifestArchive);
        writer.addFile(QStringLiteral("example.safe.forgeplugin.json"), manifestBytes);
        writer.addFile(QStringLiteral("second.forgeplugin.json"), manifestBytes);
        writer.addFile(libraryName, libraryBytes);
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    const auto multiple = PluginManager::importZip(
        multipleManifestArchive, QDir(directory.path()).filePath(QStringLiteral("multiple-target")));
    QVERIFY(!multiple.success);
    QVERIFY(multiple.error.contains(QStringLiteral("exactly one")));

    const QByteArray compressibleLibrary(2 * 1024 * 1024, '\0');
    auto bombDefinition = definition;
    bombDefinition.insert(QStringLiteral("id"), QStringLiteral("example.compressed"));
    bombDefinition.insert(
        QStringLiteral("sha256"),
        QString::fromLatin1(QCryptographicHash::hash(compressibleLibrary, QCryptographicHash::Sha256).toHex()));
    const auto bombArchive = QDir(directory.path()).filePath(QStringLiteral("compression-bomb.zip"));
    {
        ZipFixtureWriter writer(bombArchive);
        writer.setCompressionPolicy(ZipFixtureWriter::AlwaysCompress);
        writer.addFile(QStringLiteral("example.compressed.forgeplugin.json"),
                       QJsonDocument(bombDefinition).toJson(QJsonDocument::Compact));
        writer.addFile(libraryName, compressibleLibrary);
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    const auto bomb = PluginManager::importZip(
        bombArchive, QDir(directory.path()).filePath(QStringLiteral("bomb-target")));
    QVERIFY(!bomb.success);
    QVERIFY(bomb.error.contains(QStringLiteral("compression ratio")));

    auto unsafeDefinition = definition;
    unsafeDefinition.insert(QStringLiteral("name"), QStringLiteral("Trusted\nHidden permission"));
    const auto unsafeArchive = directory.filePath(QStringLiteral("unsafe-name.zip"));
    {
        ZipFixtureWriter writer(unsafeArchive);
        writer.addFile(QStringLiteral("example.safe.forgeplugin.json"), QJsonDocument(unsafeDefinition).toJson());
        writer.addFile(libraryName, libraryBytes);
        writer.close();
        QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    }
    const auto unsafeResult = PluginManager::importZip(unsafeArchive, directory.filePath(QStringLiteral("unsafe-target")));
    QVERIFY(!unsafeResult.success);
    QVERIFY(unsafeResult.error.contains(QStringLiteral("manifest identity")));

    QFile original(safeArchive);
    QVERIFY(original.open(QIODevice::ReadOnly));
    auto corrupt = original.readAll();
    auto central = corrupt.indexOf(QByteArray("PK\x01\x02", 4));
    central = corrupt.indexOf(QByteArray("PK\x01\x02", 4), central + 4); // manifest, after directory
    QVERIFY(central > 0);
    const auto local = qFromLittleEndian<quint32>(corrupt.constData() + central + 42);
    const auto wrongCrc = qFromLittleEndian<quint32>(corrupt.constData() + central + 16) ^ 1U;
    qToLittleEndian<quint32>(wrongCrc, corrupt.data() + central + 16);
    qToLittleEndian<quint32>(wrongCrc, corrupt.data() + local + 14);
    const auto corruptPath = directory.filePath(QStringLiteral("crc.zip"));
    QFile corruptFile(corruptPath);
    QVERIFY(corruptFile.open(QIODevice::WriteOnly));
    QCOMPARE(corruptFile.write(corrupt), corrupt.size());
    corruptFile.close();
    const auto corruptResult = PluginManager::importZip(corruptPath, directory.filePath(QStringLiteral("crc-target")));
    QVERIFY(!corruptResult.success);
    QVERIFY(corruptResult.error.contains(QStringLiteral("CRC")));

    for (const bool childFirst : {true, false}) {
        const auto conflictPath = directory.filePath(QStringLiteral("parent.zip"));
        ZipFixtureWriter writer(conflictPath);
        writer.addFile(QStringLiteral("example.safe.forgeplugin.json"), manifestBytes);
        writer.addFile(libraryName, libraryBytes);
        writer.addFile(childFirst ? QStringLiteral("clash/child") : QStringLiteral("clash"), QByteArray("x"));
        writer.addFile(childFirst ? QStringLiteral("clash") : QStringLiteral("clash/child"), QByteArray("x"));
        writer.close();
        const auto failure = PluginManager::importZip(conflictPath, directory.filePath(QStringLiteral("parent-target")));
        QVERIFY(!failure.success);
        QVERIFY(failure.error.contains(QStringLiteral("parent directory")));
    }
    auto wrongDigest = definition;
    wrongDigest.insert(QStringLiteral("sha256"), QString(64, u'0'));
    const auto digestPath = directory.filePath(QStringLiteral("digest.zip"));
    ZipFixtureWriter digestWriter(digestPath);
    digestWriter.addFile(QStringLiteral("example.safe.forgeplugin.json"), QJsonDocument(wrongDigest).toJson());
    digestWriter.addFile(libraryName, libraryBytes);
    digestWriter.close();
    const auto digestDestination = directory.filePath(QStringLiteral("digest-target"));
    const auto digestResult = PluginManager::importZip(digestPath, digestDestination);
    QVERIFY(!digestResult.success);
    QVERIFY(digestResult.error.contains(QStringLiteral("SHA-256")));
    QVERIFY(QDir(digestDestination).entryList(QDir::AllEntries | QDir::NoDotAndDotDot | QDir::Hidden).isEmpty());
    // Failed imports neither replace the installed plugin nor enable native code.
    QCOMPARE(manager.discover().first().sha256, libraryDigest);
    QCOMPARE(settings.value(QStringLiteral("plugins/enabledIds")).toStringList(), QStringList{});
}


void EditorCoreTests::boundedZipDecoderPolicy()
{
    // Independently produced by CPython zipfile (fixed timestamp, raw DEFLATE).
    const QList<QByteArray> fixtures{
        QByteArray::fromBase64("UEsDBBQAAAAIAAAAIVwxh73KJQAAACBOAAALAAAAcGF5bG9hZC50eHTtwTEBAAAAwqCs61/CGh5AAQAAAAAAAAAAAAAAAAAAAAAAAPBgUEsDBBQAAAAIAAAAIVwAAAAAAgAAAAAAAAAJAAAAZW1wdHkudHh0AwBQSwECFAMUAAAACAAAACFcMYe9yiUAAAAgTgAACwAAAAAAAAAAAAAAgAEAAAAAcGF5bG9hZC50eHRQSwECFAMUAAAACAAAACFcAAAAAAIAAAAAAAAACQAAAAAAAAAAAAAAgAFOAAAAZW1wdHkudHh0UEsFBgAAAAACAAIAcAAAAHcAAAAAAA=="),
        QByteArray::fromBase64("UEsDBBQACAAIAAAAIVwAAAAAAAAAAAAAAAALAAAAcGF5bG9hZC50eHTtwTEBAAAAwqCs61/CGh5AAQAAAAAAAAAAAAAAAAAAAAAAAPBgUEsHCDGHvcolAAAAIE4AAFBLAwQUAAgACAAAACFcAAAAAAAAAAAAAAAACQAAAGVtcHR5LnR4dAMAUEsHCAAAAAACAAAAAAAAAFBLAQIUAxQACAAIAAAAIVwxh73KJQAAACBOAAALAAAAAAAAAAAAAACAAQAAAABwYXlsb2FkLnR4dFBLAQIUAxQACAAIAAAAIVwAAAAAAgAAAAAAAAAJAAAAAAAAAAAAAACAAV4AAABlbXB0eS50eHRQSwUGAAAAAAIAAgBwAAAAlwAAAAAA")};
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const auto path = directory.filePath(QStringLiteral("fixture.zip"));
    const auto write = [&path](const QByteArray &bytes) {
        QFile file(path);
        return file.open(QIODevice::WriteOnly) && file.write(bytes) == bytes.size();
    };
    for (const auto &bytes : fixtures) {
        QVERIFY(write(bytes));
        SafeZipReader reader(path);
        QVERIFY2(reader.isReadable(), qPrintable(reader.errorString()));
        QCOMPARE(reader.fileData(QStringLiteral("payload.txt")), QByteArray(20000, 'a'));
        QCOMPARE(reader.fileData(QStringLiteral("empty.txt")), QByteArray{});
        QCOMPARE(reader.status(), SafeZipReader::NoError);
    }
    const auto valid = fixtures.first();
    const auto central = valid.indexOf(QByteArray("PK\x01\x02", 4));
    QVERIFY(central > 0);
    auto forged = valid;
    qToLittleEndian<quint32>(4, forged.data() + 22);
    qToLittleEndian<quint32>(4, forged.data() + central + 24);
    QVERIFY(write(forged));
    SafeZipReader bounded(path);
    QVERIFY(bounded.isReadable());
    QVERIFY(bounded.fileData(QStringLiteral("payload.txt")).isEmpty());
    QCOMPARE(bounded.status(), SafeZipReader::FileError);
    QVERIFY(bounded.errorString().contains(QStringLiteral("declared size")));

    QList<QByteArray> malformed;
    malformed.append(valid.left(valid.size() - 1));
    malformed.append(valid + QByteArray("hidden"));
    auto encrypted = valid;
    qToLittleEndian<quint16>(1, encrypted.data() + 6);
    qToLittleEndian<quint16>(1, encrypted.data() + central + 8);
    malformed.append(encrypted);
    auto zip64 = valid;
    qToLittleEndian<quint32>(0xffffffffU, zip64.data() + central + 24);
    malformed.append(zip64);
    auto mismatch = valid;
    mismatch[30] = 'x';
    malformed.append(mismatch);
    auto special = valid;
    qToLittleEndian<quint32>(0010644U << 16U, special.data() + central + 38);
    malformed.append(special);
    auto inconsistent = fixtures.last();
    qToLittleEndian<quint32>(1, inconsistent.data() + 22);
    malformed.append(inconsistent);
    for (const auto &bytes : malformed) {
        QVERIFY(write(bytes));
        SafeZipReader reader(path);
        QVERIFY2(!reader.isReadable(), "Malformed ZIP unexpectedly accepted");
    }
    ZipFixtureWriter writer(path);
    for (int index = 0; index < 257; ++index) {
        writer.addFile(QString::number(index), QByteArray{});
    }
    writer.close();
    QCOMPARE(writer.status(), ZipFixtureWriter::NoError);
    SafeZipReader crowded(path);
    QVERIFY(!crowded.isReadable());
}

void EditorCoreTests::cancellationStopsQueuedRetries()
{
    LocalJsonServer oldServer;
    LocalJsonServer newServer;
    QVERIFY(oldServer.listen(QHostAddress::LocalHost));
    QVERIFY(newServer.listen(QHostAddress::LocalHost));
    oldServer.enqueue(QByteArray("503 Service Unavailable"), QByteArray("{}"));
    ApiClient client;
    EditorPreferences preferences;
    preferences.safeGetRetries = 2;
    preferences.retryBaseDelayMs = 500;
    client.applyPreferences(preferences);
    QString error;
    const auto oldUrl = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(oldServer.serverPort()));
    const auto newUrl = QUrl(QStringLiteral("http://127.0.0.1:%1").arg(newServer.serverPort()));
    const QByteArray token("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN");
    QVERIFY(client.configure(oldUrl, token, true, &error));
    client.fetchProjects();
    QVERIFY(waitUntil([&oldServer] { return oldServer.requests.size() == 1; }));
    QTest::qWait(100); // let the 503 queue a delayed retry
    QVERIFY(client.configure(newUrl, token, true, &error));
    QTest::qWait(700);
    QCOMPARE(oldServer.requests.size(), 1);
    QCOMPARE(newServer.requests.size(), 0);
    newServer.enqueue(QByteArray("200 OK"), QByteArray("{}"), {}, 1500);
    QSignalSpy failed(&client, &ApiClient::requestFailedDetailed);
    client.fetchProjects();
    QVERIFY(waitUntil([&newServer] { return newServer.requests.size() == 1; }));
    client.cancelActiveRequests();
    QVERIFY(waitUntil([&failed] { return failed.size() == 1; }));
    QCOMPARE(failed.at(0).at(3).toString(), QStringLiteral("canceled"));
    QTest::qWait(700);
    QCOMPARE(newServer.requests.size(), 1);
}

void EditorCoreTests::graphModelPolicyAndCompiler()
{
    GraphModel graph;
    const auto request = graph.addNode(QStringLiteral("request.input"), QStringLiteral("Request"), QPointF(0, 0),
                                       QJsonObject{{QStringLiteral("method"), QStringLiteral("POST")}});
    const auto policy = graph.addNode(QStringLiteral("auth.policy"), QStringLiteral("Policy"), QPointF(280, 0),
                                      QJsonObject{{QStringLiteral("permission"), QStringLiteral("orders.create")}});
    const auto query = graph.addNode(QStringLiteral("data.query"), QStringLiteral("Query"), QPointF(560, 0),
                                     QJsonObject{{QStringLiteral("sql"), QStringLiteral("SELECT :id AS id")},
                                                 {QStringLiteral("mode"), QStringLiteral("fetch_one")},
                                                 {QStringLiteral("params"), QJsonObject{{QStringLiteral("id"), QStringLiteral("$body.id")}}}});
    const auto operation = graph.addNode(QStringLiteral("operation.call"), QStringLiteral("Operation"), QPointF(840, 0),
                                         QJsonObject{{QStringLiteral("name"), QStringLiteral("orders.lookup")},
                                                     {QStringLiteral("method"), QStringLiteral("POST")}});
    QVERIFY(!request.isEmpty());
    QVERIFY(!policy.isEmpty());
    QVERIFY(!query.isEmpty());
    QVERIFY(!operation.isEmpty());
    QString error;
    QVERIFY2(graph.connectNodes(request, QStringLiteral("exec"), policy, QStringLiteral("exec"), &error), qPrintable(error));
    QVERIFY2(graph.connectNodes(policy, QStringLiteral("exec"), query, QStringLiteral("exec"), &error), qPrintable(error));
    QVERIFY2(graph.connectNodes(query, QStringLiteral("exec"), operation, QStringLiteral("exec"), &error), qPrintable(error));
    QVERIFY2(graph.validate(&error), qPrintable(error));
    const auto compiled = graph.compiledFragment(&error);
    QVERIFY2(!compiled.isEmpty(), qPrintable(error));
    const auto compiledOperation = compiled.value(QStringLiteral("operations")).toArray().first().toObject();
    QCOMPARE(compiledOperation.value(QStringLiteral("name")).toString(), QStringLiteral("orders.lookup"));
    QCOMPARE(compiledOperation.value(QStringLiteral("permission")).toString(), QStringLiteral("orders.create"));
    QCOMPARE(compiledOperation.value(QStringLiteral("statements")).toArray().size(), 1);
}

void EditorCoreTests::graphCycleRollback()
{
    GraphModel graph;
    const auto first = graph.addNode(QStringLiteral("logic.branch"), QStringLiteral("First"), QPointF(0, 0));
    const auto second = graph.addNode(QStringLiteral("transform.map"), QStringLiteral("Second"), QPointF(280, 0));
    QString error;
    QVERIFY(graph.connectNodes(first, QStringLiteral("exec"), second, QStringLiteral("exec"), &error));
    QVERIFY(!graph.connectNodes(second, QStringLiteral("exec"), first, QStringLiteral("exec"), &error));
    QVERIFY(error.contains(QStringLiteral("acyclic")));
    QCOMPARE(graph.document().value(QStringLiteral("edges")).toArray().size(), 1);
}

void EditorCoreTests::graphCompilerRejectsDesignOnlyNodes()
{
    GraphModel graph;
    const auto branch = graph.addNode(QStringLiteral("logic.branch"), QStringLiteral("Guard"), QPointF(0, 0));
    const auto query = graph.addNode(QStringLiteral("data.query"), QStringLiteral("Query"), QPointF(280, 0),
                                     QJsonObject{{QStringLiteral("sql"), QStringLiteral("SELECT 1")}});
    const auto operation = graph.addNode(QStringLiteral("operation.call"), QStringLiteral("Operation"), QPointF(560, 0),
                                         QJsonObject{{QStringLiteral("name"), QStringLiteral("guarded.operation")}});
    QString error;
    QVERIFY(graph.connectNodes(branch, QStringLiteral("exec"), query, QStringLiteral("exec"), &error));
    QVERIFY(graph.connectNodes(query, QStringLiteral("exec"), operation, QStringLiteral("exec"), &error));
    QVERIFY(graph.compiledFragment(&error).isEmpty());
    QVERIFY(error.contains(QStringLiteral("design-only")));
    QVERIFY(error.contains(QStringLiteral("not silently omitted")));
}

void EditorCoreTests::pythonSdkInstallPackageIdentity()
{
    PythonSdkPanel panel;
    auto *mode = panel.findChild<QComboBox *>();
    auto *install = panel.findChild<QLabel *>(QStringLiteral("sdkInstallCard"));
    QVERIFY(mode != nullptr && install != nullptr);
    for (int index = 0; index < mode->count(); ++index) {
        mode->setCurrentIndex(index);
        const auto extra = mode->currentData().toString();
        if (extra == QStringLiteral("younglion") || extra == QStringLiteral("ddm")) {
            QCOMPARE(install->text(), QStringLiteral("Install:  pip install \"json-api-forge-client[%1]\"").arg(extra));
        } else {
            QCOMPARE(install->text(), QStringLiteral("Install:  pip install json-api-forge-client"));
        }
    }
}

void EditorCoreTests::pythonSdkFailedStartAllowsRetry()
{
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
#ifdef Q_OS_WIN
    const auto name = QStringLiteral("python3.exe");
#else
    const auto name = QStringLiteral("python3");
#endif
    QFile invalidInterpreter(directory.filePath(name));
    QVERIFY(invalidInterpreter.open(QIODevice::WriteOnly));
    invalidInterpreter.write("This is not an executable interpreter.\n");
    invalidInterpreter.close();
    QVERIFY(invalidInterpreter.setPermissions(QFile::ReadOwner | QFile::WriteOwner | QFile::ExeOwner));
    const auto previousPath = qgetenv("PATH");
    const auto restorePath = qScopeGuard([previousPath] { qputenv("PATH", previousPath); });
    qputenv("PATH", directory.path().toLocal8Bit());
    PythonSdkPanel panel;
    QPushButton *check = nullptr;
    for (auto *button : panel.findChildren<QPushButton *>()) {
        if (button->text() == QStringLiteral("Check installed SDK")) { check = button; }
    }
    QVERIFY(check != nullptr);
    QSignalSpy messages(&panel, &PythonSdkPanel::statusMessage);
    check->click();
    QVERIFY(waitUntil([&] { return messages.count() == 1; }));
    QVERIFY(messages.at(0).at(0).toString().contains(QStringLiteral("could not start")));
    check->click();
    QVERIFY(waitUntil([&] { return messages.count() == 2; }));
    QVERIFY(messages.at(1).at(0).toString().contains(QStringLiteral("could not start")));
}

void EditorCoreTests::pythonSdkSnippetPolicy()
{
    const PythonSdkSettings sync{QStringLiteral("sync"), QStringLiteral("https://forge.example.com"), QString(),
                                 QStringLiteral("enterprise"), QStringLiteral("records"), QStringLiteral("records.summary"),
                                 QStringLiteral("FORGE_API_KEY")};
    QString error;
    const auto code = PythonSdkPanel::generatedSnippet(sync, &error);
    QVERIFY2(!code.isEmpty(), qPrintable(error));
    QVERIFY(code.contains(QStringLiteral("RetryPolicy")));
    QVERIFY(code.contains(QStringLiteral("iter_items")));
    auto unsafe = sync;
    unsafe.endpoint = QStringLiteral("https://user:secret@forge.example.com");
    QVERIFY(PythonSdkPanel::generatedSnippet(unsafe, &error).isEmpty());
    auto cluster = sync;
    cluster.mode = QStringLiteral("cluster");
    cluster.clusterEndpoints = QStringLiteral("https://eu.example.com,https://us.example.com");
    const auto clusterCode = PythonSdkPanel::generatedSnippet(cluster, &error);
    QVERIFY2(clusterCode.contains(QStringLiteral("RoutingStrategy.RENDEZVOUS")), qPrintable(error));
    for (const auto &mode : {QStringLiteral("sync"), QStringLiteral("async"), QStringLiteral("ddm"), QStringLiteral("younglion")}) {
        auto local = sync;
        local.mode = mode;
        local.endpoint = QStringLiteral("http://127.0.0.1:8000");
        const auto localCode = PythonSdkPanel::generatedSnippet(local, &error);
        QVERIFY2(localCode.contains(QStringLiteral("allow_insecure_http=True")), qPrintable(error));
        local.endpoint = QStringLiteral("http://remote.example.com");
        QVERIFY(PythonSdkPanel::generatedSnippet(local, &error).isEmpty());
    }
    cluster.clusterEndpoints = QStringLiteral("http://localhost:8000,https://remote.example.com");
    const auto mixedCode = PythonSdkPanel::generatedSnippet(cluster, &error);
    QVERIFY2(!mixedCode.isEmpty(), qPrintable(error));
    QCOMPARE(mixedCode.count(QStringLiteral("allow_insecure_http=True")), 1);
}

void EditorCoreTests::forgePluginCatalogPolicy()
{
    QUrl endpoint;
    QString error;
    QVERIFY(PluginCatalogClient::catalogEndpoint(QUrl(QStringLiteral("https://forge.example.com")),
                                                  QStringLiteral("editor-plugin-registry"), QStringLiteral("editor/plugins"),
                                                  false, &endpoint, &error));
    QCOMPARE(endpoint.path(), QStringLiteral("/api/editor-plugin-registry/v1/editor/plugins"));
    QVERIFY(!PluginCatalogClient::catalogEndpoint(QUrl(QStringLiteral("https://forge.example.com")),
                                                   QStringLiteral("../admin"), QStringLiteral("editor/plugins"), false,
                                                   &endpoint, &error));
    const QJsonArray valid{QJsonObject{{QStringLiteral("plugin_id"), QStringLiteral("vendor.analytics")},
                                       {QStringLiteral("name"), QStringLiteral("Analytics")},
                                       {QStringLiteral("version"), QStringLiteral("1.2.0")},
                                       {QStringLiteral("sha256"), QString(64, u'a')},
                                       {QStringLiteral("download_url"), QStringLiteral("https://packages.example.com/analytics.zip")},
                                       {QStringLiteral("permissions"), QJsonArray{QStringLiteral("graph.nodes.register")}}}};
    QVERIFY2(PluginCatalogClient::validateCatalog(valid, &error), qPrintable(error));
    auto invalid = valid;
    auto item = invalid.first().toObject();
    item.insert(QStringLiteral("download_url"), QStringLiteral("http://packages.example.com/analytics.zip"));
    invalid.replace(0, item);
    QVERIFY(!PluginCatalogClient::validateCatalog(invalid, &error));
}

void EditorCoreTests::embeddedProjectTemplates()
{
    QString error;
    const auto definitions = TemplateManager::templates(&error);
    QVERIFY2(definitions.size() >= 8, qPrintable(error));
    QTemporaryDir workspace;
    QVERIFY(workspace.isValid());
    QVERIFY2(TemplateManager::createProject(definitions.first(), workspace.path(), QStringLiteral("TemplateProject"),
                                             QStringLiteral("template-project"), &error),
             qPrintable(error));
    const QDir project(workspace.filePath(QStringLiteral("TemplateProject")));
    QVERIFY(QFileInfo::exists(project.filePath(QStringLiteral("app.json"))));
    QVERIFY(QFileInfo::exists(project.filePath(QStringLiteral("config/40-resources.json"))));
    QFile graphFile(project.filePath(QStringLiteral("graphs/domain-flow.forgegraph.json")));
    QVERIFY(graphFile.open(QIODevice::ReadOnly));
    QJsonObject graphDocument;
    QVERIFY(DocumentCodec::parseObject(graphFile.readAll(), &graphDocument, &error));
    GraphModel graph;
    QVERIFY2(graph.setDocument(graphDocument, &error), qPrintable(error));
    QVERIFY(!graph.compiledFragment(&error).isEmpty());
    QVERIFY(!TemplateManager::createProject(definitions.first(), workspace.path(), QStringLiteral("TemplateProject"),
                                             QStringLiteral("template-project"), &error));
}

QTEST_MAIN(EditorCoreTests)
#include "tst_editor_core.moc"
