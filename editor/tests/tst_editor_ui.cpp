#include "ApiClient.hpp"
#include "ChatView.hpp"
#include "CodeEditor.hpp"
#include "DocumentCodec.hpp"
#include "EditorSettings.hpp"
#include "MainWindow.hpp"
#include "NodeGraphEditor.hpp"
#include "TeamWorkspace.hpp"
#include "VisualDesigner.hpp"

#include <QApplication>
#include <QCryptographicHash>
#include <QMimeData>
#include <QDialogButtonBox>
#include <QComboBox>
#include <QSpinBox>
#include <QDockWidget>
#include <QFile>
#include <QHostAddress>
#include <QElapsedTimer>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMessageBox>
#include <QPushButton>
#include <QPlainTextEdit>
#include <QTabWidget>
#include <QSettings>
#include <QSharedPointer>
#include <QSignalSpy>
#include <QSplitter>
#include <QStackedWidget>
#include <QTableWidget>
#include <QTcpServer>
#include <QTcpSocket>
#include <QTemporaryDir>
#include <QTest>
#include <QTextEdit>
#include <QTimer>
#include <QVariantAnimation>
#include <QToolButton>
#include <QTreeWidget>

#include <functional>

namespace {
const QByteArray TestSession("jfe_session_9M2vK7pQ4xR8sT6wY3nC5aH1dL0uB7eF9qA2sD4gH6jK8mN");

// Qt 6.8's QTRY macros narrow chrono::rep under -Wconversion on LP64.
bool waitUntil(const std::function<bool()> &condition, int timeoutMs = 5000)
{
    QElapsedTimer timer;
    timer.start();
    while (!condition() && timer.elapsed() < timeoutMs) { QTest::qWait(10); }
    return condition();
}

class WorkspaceServer final : public QTcpServer {
public:
    struct Response {
        QByteArray body = QByteArray("{}");
        QByteArray status = QByteArray("200 OK");
        int delay = 0;
    };
    QList<QByteArray> requests;
    std::function<Response(const QByteArray &)> respond;

    WorkspaceServer()
    {
        connect(this, &QTcpServer::newConnection, this, [this] {
            while (hasPendingConnections()) {
                auto *socket = nextPendingConnection();
                connect(socket, &QTcpSocket::disconnected, socket, &QObject::deleteLater);
                const auto bytes = QSharedPointer<QByteArray>::create();
                connect(socket, &QTcpSocket::readyRead, this, [this, socket, bytes] {
                    bytes->append(socket->readAll());
                    const auto headerEnd = bytes->indexOf("\r\n\r\n");
                    if (headerEnd < 0 || socket->property("responded").toBool()) { return; }
                    qint64 length = 0;
                    for (const auto &line : bytes->left(headerEnd).split('\n')) {
                        if (line.toLower().startsWith("content-length:")) { length = line.mid(15).trimmed().toLongLong(); }
                    }
                    if (bytes->size() < headerEnd + 4 + length) { return; }
                    socket->setProperty("responded", true);
                    requests.append(*bytes);
                    const auto response = respond ? respond(*bytes) : Response{};
                    const auto output = QByteArray("HTTP/1.1 ") + response.status
                        + QByteArray("\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ")
                        + QByteArray::number(response.body.size()) + QByteArray("\r\n\r\n") + response.body;
                    QTimer::singleShot(response.delay, socket, [socket, output] {
                        socket->write(output);
                        socket->disconnectFromHost();
                    });
                });
            }
        });
    }

    QUrl url() const { return QUrl(QStringLiteral("http://127.0.0.1:%1").arg(serverPort())); }
};

QJsonObject capabilities(const QStringList &permissions)
{
    QJsonArray values;
    for (const auto &permission : permissions) { values.append(permission); }
    return {{QStringLiteral("collaboration"), true}, {QStringLiteral("database_browser"), true},
            {QStringLiteral("calls"), true}, {QStringLiteral("rank"), 100},
            {QStringLiteral("permissions"), values}};
}

QJsonObject spaces()
{
    return {{QStringLiteral("areas"), QJsonArray{
        QJsonObject{{QStringLiteral("id"), QStringLiteral("alpha")}, {QStringLiteral("name"), QStringLiteral("Alpha")}},
        QJsonObject{{QStringLiteral("id"), QStringLiteral("beta")}, {QStringLiteral("name"), QStringLiteral("Beta")}}}}};
}

QJsonObject messages(const QString &body)
{
    return {{QStringLiteral("messages"), QJsonArray{QJsonObject{{QStringLiteral("body"), body}}}}};
}

void deliver(ApiClient &api, const QString &operation, const QJsonObject &payload)
{
    api.jsonReceived(operation, payload);
}

QTableWidgetItem *propertyValue(VisualDesigner *designer, const QString &key)
{
    auto *table = designer->findChild<QTableWidget *>(QStringLiteral("propertyTable"));
    for (int row = 0; row < table->rowCount(); ++row) {
        if (table->item(row, 0)->text() == key) { return table->item(row, 1); }
    }
    return nullptr;
}
} // namespace

class EditorUiTests final : public QObject {
    Q_OBJECT
private slots:
    void initTestCase();
    void cleanup();
    void sidebarReclaimsSpace();
    void sidebarResponsivePreference();
    void compactTeamKeepsNavigation();
    void disconnectRevokesSession();
    void repeatedVisualModePreservesEdits();
    void repeatedGraphModePreservesEdits();
    void openingDocumentDoesNotSerializePreviousMode();
    void visualPropertyTypesAndPalette();
    void visualNestedEditsUndoAndReorder();
    void visualStructuredAndNewPropertyDialogs();
    void spacesExplainAvailabilityAndKeepComposerVisible();
    void founderStartupRecoversCapabilityFailure();
    void cancelledProjectSelectionRestoresSelection();
    void staleDocumentResponsesAreIgnored();
    void workerRefreshRespectsPermissionsAndGlobalScope();
    void failedMessagePreservesDraftAndPreventsDuplicatePosts();
    void workspaceIgnoresStaleResponsesAndKeepsAreaDrafts();
    void noteAcknowledgementPreservesNewerEdits();
    void resetClearsSessionData();
    void founderWildcardPermissions();
    void restrictedNotesCarryReaderRank();
    void conversationKeepsSelectionAndEscapesContent();
    void composerSupportsMultilineAndFilePaste();
    void backgroundRefreshAndDownloadIntegrity();
    void liveServerContract();

private:
    QTemporaryDir m_settings;
};

void EditorUiTests::initTestCase()
{
    QVERIFY(m_settings.isValid());
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, m_settings.path());
    QCoreApplication::setOrganizationName(QStringLiteral("Cavanshirpro"));
    QCoreApplication::setApplicationName(QStringLiteral("JSON API Forge Editor"));
}

void EditorUiTests::cleanup()
{
    QSettings().clear();
}

void EditorUiTests::sidebarReclaimsSpace()
{
    MainWindow window(nullptr, false);
    window.resize(1280, 800);
    window.show();
    QTest::qWait(30);
    auto *splitter = window.findChild<QSplitter *>(QStringLiteral("rootSplitter"));
    auto *sidebar = window.findChild<QWidget *>(QStringLiteral("sidebar"));
    const int original = sidebar->width();
    QVERIFY(original >= 220);
    window.toggleSidebar();
    QVERIFY(window.m_sidebarAnimation != nullptr);
    // Verify the intermediate layout without depending on a busy runner's frame timing.
    window.m_sidebarAnimation->pause();
    window.m_sidebarAnimation->setCurrentTime(window.m_sidebarAnimation->duration() / 2);
    QVERIFY(sidebar->isVisible());
    QVERIFY(sidebar->width() > 0 && sidebar->width() < original);
    QVERIFY(splitter->widget(1)->width() > splitter->width() - original);
    window.m_sidebarAnimation->resume();
    QVERIFY(waitUntil([&] { return sidebar->isHidden(); }));
    QVERIFY(waitUntil([&] { return (splitter->widget(1)->geometry()) == (splitter->contentsRect()); }));
    QCOMPARE(splitter->widget(1)->geometry(), splitter->contentsRect());
    window.toggleSidebar();
    QVERIFY(waitUntil([&] { return sidebar->isVisible() && sidebar->width() == original; }));
    QCOMPARE(sidebar->width(), original);
    for (int count = 0; count < 8; ++count) { window.toggleSidebar(); }
    QVERIFY(waitUntil([&] { return sidebar->isVisible() && sidebar->width() == original; }));
    QCOMPARE(sidebar->width(), original);
}

void EditorUiTests::sidebarResponsivePreference()
{
    MainWindow window(nullptr, false);
    window.show();
    window.resize(1280, 800);
    QTest::qWait(20);
    window.resize(820, 640);
    QVERIFY(waitUntil([&] { return window.m_sidebar->isHidden(); }));
    window.resize(1280, 800);
    QVERIFY(waitUntil([&] { return window.m_sidebar->isVisible(); }));
    window.toggleSidebar();
    QVERIFY(waitUntil([&] { return window.m_sidebar->isHidden(); }));
    window.resize(820, 640);
    window.resize(1280, 800);
    QVERIFY(window.m_sidebar->isHidden());
}

void EditorUiTests::compactTeamKeepsNavigation()
{
    MainWindow window(nullptr, false);
    window.resize(1024, 640);
    window.show();
    window.showTeamPreview();
    QVERIFY(waitUntil([&] { return window.centralWidget()->isVisible(); }));
    QVERIFY(window.m_teamDock->isVisible());
    QVERIFY(window.m_sidebarButton->isVisible());
}

void EditorUiTests::disconnectRevokesSession()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    server.respond = [](const QByteArray &) { return WorkspaceServer::Response{QByteArray(), QByteArray("204 No Content"), 0}; };
    MainWindow window(nullptr, false);
    QString error;
    QVERIFY(window.m_api->configure(server.url(), TestSession, true, &error));
    window.m_remoteMode = true;
    window.updateConnectionActions();
    window.show();
    QVERIFY(window.m_disconnectButton->isVisible());
    QVERIFY(window.m_disconnectButton->parentWidget()->objectName() == QStringLiteral("workspaceHeader"));
    window.disconnectServer();
    QVERIFY(waitUntil([&] { return (server.requests.size()) == (1); }));
    QCOMPARE(server.requests.size(), 1);
    QVERIFY(server.requests.first().startsWith("POST /__forge/editor/v1/auth/logout "));
    QByteArray authorization;
    for (const auto &line : server.requests.first().split('\n')) {
        if (line.left(14).toLower() == QByteArray("authorization:")) { authorization = line.mid(14).trimmed(); }
    }
    QCOMPARE(authorization, QByteArray("Bearer ") + TestSession);
    QVERIFY(!window.m_api->isConfigured());
    QVERIFY(window.m_disconnectButton->isHidden());
}

void EditorUiTests::repeatedVisualModePreservesEdits()
{
    MainWindow window(nullptr, false);
    window.loadDocument(QStringLiteral("config/resources.json"), QByteArray(R"({"resources":[{"path":"items","auto_create":false}]})"), QStringLiteral("revision"));
    window.showVisualMode();
    auto *tree = window.m_visualDesigner->findChild<QTreeWidget *>(QStringLiteral("designerCanvas"));
    tree->setCurrentItem(tree->topLevelItem(0)->child(0));
    auto *value = propertyValue(window.m_visualDesigner, QStringLiteral("path"));
    QVERIFY(value != nullptr);
    value->setText(QStringLiteral("orders"));
    const auto edited = window.m_visualDesigner->document();
    window.showVisualMode();
    QCOMPARE(window.m_visualDesigner->document(), edited);
    QVERIFY(window.m_dirty);
    window.showCodeMode();
    window.showVisualMode();
    QCOMPARE(window.m_visualDesigner->document(), edited);
}

void EditorUiTests::repeatedGraphModePreservesEdits()
{
    MainWindow window(nullptr, false);
    window.loadDocument(QStringLiteral("graphs/first.forgegraph.json"), DocumentCodec::prettyJson(NodeGraphEditor::starterDocument(QStringLiteral("config/first.json"))), QStringLiteral("first"));
    auto edited = window.m_graphEditor->document();
    edited.insert(QStringLiteral("target_document"), QStringLiteral("config/changed.json"));
    QString error;
    QVERIFY(window.m_graphEditor->setDocument(edited, &error));
    window.showGraphMode();
    QCOMPARE(window.m_graphEditor->document(), edited);
}

void EditorUiTests::openingDocumentDoesNotSerializePreviousMode()
{
    MainWindow window(nullptr, false);
    window.loadDocument(QStringLiteral("config/first.json"), QByteArray(R"({"resources":[{"path":"first"}]})"), QStringLiteral("first"));
    window.showVisualMode();
    window.loadDocument(QStringLiteral("config/second.json"), QByteArray(R"({"resources":[{"path":"second"}]})"), QStringLiteral("second"));
    QVERIFY(window.m_codeEditor->toPlainText().contains(QStringLiteral("second")));
    window.loadDocument(QStringLiteral("graphs/first.forgegraph.json"), DocumentCodec::prettyJson(NodeGraphEditor::starterDocument(QStringLiteral("config/first.json"))), QStringLiteral("first"));
    window.loadDocument(QStringLiteral("graphs/second.forgegraph.json"), DocumentCodec::prettyJson(NodeGraphEditor::starterDocument(QStringLiteral("config/second.json"))), QStringLiteral("second"));
    QCOMPARE(window.m_graphEditor->document().value(QStringLiteral("target_document")).toString(), QStringLiteral("config/second.json"));
}

void EditorUiTests::visualPropertyTypesAndPalette()
{
    VisualDesigner designer;
    designer.setDocument(QJsonObject{{QStringLiteral("resources"), QJsonArray{QJsonObject{{QStringLiteral("path"), QStringLiteral("items")}, {QStringLiteral("auto_create"), false}}}}});
    auto *tree = designer.findChild<QTreeWidget *>(QStringLiteral("designerCanvas"));
    tree->setCurrentItem(tree->topLevelItem(0)->child(0));
    propertyValue(&designer, QStringLiteral("path"))->setText(QStringLiteral("123"));
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().first().toObject().value(QStringLiteral("path")).type(), QJsonValue::String);
    QCOMPARE(tree->currentItem()->text(0), QStringLiteral("123"));
    QSignalSpy errors(&designer, &VisualDesigner::statusMessage);
    propertyValue(&designer, QStringLiteral("auto_create"))->setText(QStringLiteral("invalid"));
    QCOMPARE(errors.size(), 1);
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().first().toObject().value(QStringLiteral("auto_create")).toBool(), false);
    auto *palette = designer.findChild<QListWidget *>(QStringLiteral("componentPalette"));
    designer.setDocument(QJsonObject{{QStringLiteral("resources"), QStringLiteral("preserve me")}});
    palette->itemDoubleClicked(palette->item(0));
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toString(), QStringLiteral("preserve me"));
    designer.setDocument(QJsonObject{});
    palette->itemDoubleClicked(palette->item(0));
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().size(), 1);
}

void EditorUiTests::visualNestedEditsUndoAndReorder()
{
    VisualDesigner designer;
    const QJsonObject original{{QStringLiteral("resources"), QJsonArray{QJsonObject{
        {QStringLiteral("path"), QStringLiteral("items")},
        {QStringLiteral("columns"), QJsonObject{{QStringLiteral("price"), 10.5}}},
        {QStringLiteral("allowed_actions"), QJsonArray{QStringLiteral("read"), QStringLiteral("list")}}}}},
        {QStringLiteral("name"), QStringLiteral("Original")}};
    designer.setDocument(original);
    auto *tree = designer.findChild<QTreeWidget *>(QStringLiteral("designerCanvas"));
    auto *resource = tree->topLevelItem(1)->child(0);
    tree->setCurrentItem(resource->child(1)); // columns object (keys are sorted)
    propertyValue(&designer, QStringLiteral("price"))->setText(QStringLiteral("19.25"));
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().first().toObject().value(QStringLiteral("columns")).toObject().value(QStringLiteral("price")).toDouble(), 19.25);
    designer.undo();
    QCOMPARE(designer.document(), original);
    designer.redo();
    resource = tree->topLevelItem(1)->child(0);
    tree->setCurrentItem(resource->child(0)->child(0)); // first scalar array item
    propertyValue(&designer, QStringLiteral("Value"))->setText(QStringLiteral("delete"));
    designer.moveSelectionDown();
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().first().toObject().value(QStringLiteral("allowed_actions")).toArray(), (QJsonArray{QStringLiteral("list"), QStringLiteral("delete")}));
    tree->setCurrentItem(tree->topLevelItem(1)->child(0));
    designer.duplicateSelection();
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().at(1).toObject().value(QStringLiteral("path")).toString(), QStringLiteral("items-2"));
    designer.removeSelection();
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().size(), 1);
    designer.undo();
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().size(), 2);
    designer.findChild<QPushButton *>(QStringLiteral("visualRoot"))->click();
    propertyValue(&designer, QStringLiteral("name"))->setText(QStringLiteral("Changed"));
    QCOMPARE(designer.document().value(QStringLiteral("name")).toString(), QStringLiteral("Changed"));
    QVERIFY(!designer.findChild<QPushButton *>(QStringLiteral("visualRedo"))->isEnabled());
    auto *palette = designer.findChild<QListWidget *>(QStringLiteral("componentPalette"));
    palette->itemDoubleClicked(palette->item(0));
    QCOMPARE(designer.document().value(QStringLiteral("resources")).toArray().last().toObject().value(QStringLiteral("path")).toString(), QStringLiteral("items-3"));
    designer.findChild<QLineEdit *>(QStringLiteral("componentSearch"))->setText(QStringLiteral("Realtime"));
    QVERIFY(palette->item(0)->isHidden());
    QVERIFY(!palette->item(3)->isHidden());
    designer.findChild<QLineEdit *>(QStringLiteral("structureSearch"))->setText(QStringLiteral("19.25"));
    QVERIFY(tree->topLevelItem(0)->isHidden());
    QVERIFY(!tree->topLevelItem(1)->isHidden());
}

void EditorUiTests::visualStructuredAndNewPropertyDialogs()
{
    VisualDesigner designer;
    designer.setDocument({{QStringLiteral("settings"), QJsonObject{{QStringLiteral("enabled"), true}}}});
    auto *table = designer.findChild<QTableWidget *>(QStringLiteral("propertyTable"));
    bool corrected = false;
    QTimer::singleShot(0, [&] {
        auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
        if (dialog == nullptr) { return; }
        auto *editor = dialog->findChild<QPlainTextEdit *>(QStringLiteral("structuredPropertyEditor"));
        auto *buttons = dialog->findChild<QDialogButtonBox *>();
        editor->setPlainText(QStringLiteral("[]")); // mismatched type must stay in the dialog
        buttons->button(QDialogButtonBox::Save)->click();
        corrected = dialog->isVisible() && editor->toPlainText() == QStringLiteral("[]");
        editor->setPlainText(QStringLiteral("{\"enabled\":false,\"nested\":[1,\"two\"]}"));
        buttons->button(QDialogButtonBox::Save)->click();
    });
    table->cellDoubleClicked(0, 1);
    QVERIFY(corrected);
    QCOMPARE(designer.document().value(QStringLiteral("settings")).toObject().value(QStringLiteral("enabled")).toBool(), false);
    bool duplicateRejected = false;
    QTimer::singleShot(0, [&] {
        auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget());
        if (dialog == nullptr) { return; }
        auto *name = dialog->findChild<QLineEdit *>(QStringLiteral("newPropertyName"));
        auto *type = dialog->findChild<QComboBox *>(QStringLiteral("newPropertyType"));
        auto *buttons = dialog->findChild<QDialogButtonBox *>();
        name->setText(QStringLiteral("settings"));
        buttons->button(QDialogButtonBox::Ok)->click();
        duplicateRejected = dialog->isVisible();
        name->setText(QStringLiteral("newCount"));
        type->setCurrentIndex(1);
        buttons->button(QDialogButtonBox::Ok)->click();
    });
    designer.findChild<QPushButton *>(QStringLiteral("visualAddProperty"))->click();
    QVERIFY(duplicateRejected);
    QVERIFY(designer.document().value(QStringLiteral("newCount")).isDouble());
    const auto row = propertyValue(&designer, QStringLiteral("newCount"))->row();
    table->setCurrentCell(row, 0);
    designer.findChild<QPushButton *>(QStringLiteral("visualRemoveProperty"))->click();
    QVERIFY(!designer.document().contains(QStringLiteral("newCount")));
    designer.undo();
    QVERIFY(designer.document().contains(QStringLiteral("newCount")));
}

void EditorUiTests::spacesExplainAvailabilityAndKeepComposerVisible()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    ApiClient api;
    QString error;
    QVERIFY(api.configureServer(server.url(), true, &error));
    TeamWorkspace team(&api);
    auto *tabs = team.findChild<QTabWidget *>(QStringLiteral("teamTabs"));
    auto *availability = team.findChild<QLabel *>(QStringLiteral("spaceAvailability"));
    auto *retry = team.findChild<QPushButton *>(QStringLiteral("retrySpacesConnection"));
    QVERIFY(tabs->isTabEnabled(0));
    QVERIFY(availability->text().contains(QStringLiteral("Sign in")));
    QVERIFY(api.setSessionToken(TestSession, &error));
    team.setCapabilities({});
    QVERIFY(availability->text().contains(QStringLiteral("Loading")));
    team.setAvailabilityError(QStringLiteral("HTTP 503 temporarily unavailable"));
    QVERIFY(availability->text().contains(QStringLiteral("503")));
    QSignalSpy retries(&team, &TeamWorkspace::retryConnectionRequested);
    retry->click();
    QCOMPARE(retries.size(), 1);
    auto disabled = capabilities({QStringLiteral("*")});
    disabled.insert(QStringLiteral("collaboration"), false);
    team.setCapabilities(disabled);
    QVERIFY(availability->text().contains(QStringLiteral("EDITOR_COLLABORATION_ENABLED")));
    QVERIFY(tabs->isTabEnabled(0));
    team.setCapabilities({{QStringLiteral("api_version"), 1}});
    QVERIFY(availability->text().contains(QStringLiteral("Update")));
    team.setCapabilities(capabilities({QStringLiteral("*")}));
    deliver(api, QStringLiteral("team-areas:*"), spaces());
    auto *composer = team.findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    QVERIFY(composer->isEnabled());
    for (auto *button : team.findChildren<QPushButton *>()) {
        if (button->property("forgePermission") == QStringLiteral("calls.start")) { QVERIFY(button->isEnabled()); }
    }
    team.resize(760, 350);
    team.show();
    QTest::qWait(40);
    QVERIFY(composer->isVisible());
    QVERIFY(team.rect().contains(QRect(composer->mapTo(&team, QPoint(0, 0)), composer->size())));
    const auto actual = team.size();
    QVERIFY2(actual.height() <= 360, qPrintable(QStringLiteral("Team minimum height inflated to %1").arg(actual.height())));
    QVERIFY(team.findChild<QTreeWidget *>(QStringLiteral("attachmentList"))->isHidden());
    team.findChild<QPushButton *>(QStringLiteral("toggleSharedFiles"))->click();
    QVERIFY(team.findChild<QTreeWidget *>(QStringLiteral("attachmentList"))->isVisible());
}

void EditorUiTests::founderStartupRecoversCapabilityFailure()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    server.respond = [](const QByteArray &request) {
        if (request.startsWith("GET /__forge/editor/v1/capabilities?project=")) {
            return WorkspaceServer::Response{QJsonDocument(capabilities({QStringLiteral("*")})).toJson()};
        }
        if (request.startsWith("GET /__forge/editor/v1/capabilities ")) {
            return WorkspaceServer::Response{QByteArray("{\"detail\":\"temporarily unavailable\"}"), QByteArray("503 Service Unavailable")};
        }
        if (request.startsWith("GET /__forge/editor/v1/projects ")) {
            return WorkspaceServer::Response{QByteArray("{\"projects\":[{\"name\":\"Workspace\",\"directory\":\"Workspace\"}]}")};
        }
        if (request.startsWith("GET /__forge/editor/v1/areas?")) {
            return WorkspaceServer::Response{QJsonDocument(spaces()).toJson()};
        }
        return WorkspaceServer::Response{};
    };
    MainWindow window(nullptr, false);
    window.m_remoteMode = true;
    QString error;
    QVERIFY(window.m_api->configureServer(server.url(), true, &error));
    QVERIFY(window.m_api->setSessionToken(TestSession, &error));
    deliver(*window.m_api, QStringLiteral("auth-login"), {{QStringLiteral("profile"), QJsonObject{{QStringLiteral("username"), QStringLiteral("founder")}}}});
    QVERIFY(waitUntil([&] { return window.m_currentProject == QStringLiteral("Workspace"); }));
    auto *composer = window.m_teamWorkspace->findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    QVERIFY(waitUntil([&] { return composer->isEnabled(); }));
    QVERIFY(window.m_teamWorkspace->findChild<QTabWidget *>(QStringLiteral("teamTabs"))->isTabEnabled(0));
    QVERIFY(window.windowTitle().contains(QStringLiteral("v0.5.2")));
}

void EditorUiTests::cancelledProjectSelectionRestoresSelection()
{
    QTemporaryDir workspace;
    QVERIFY(workspace.isValid());
    for (const auto &name : {QStringLiteral("Alpha"), QStringLiteral("Beta")}) {
        QDir().mkpath(workspace.filePath(name));
        QFile file(workspace.filePath(name + QStringLiteral("/app.json")));
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write("{}");
    }
    MainWindow window(nullptr, false);
    window.populateLocalProjects(workspace.path());
    const auto original = window.m_currentProject;
    window.loadDocument(QStringLiteral("app.json"), QByteArray("{}"), QStringLiteral("initial"));
    window.setDirty(true);
    QTimer::singleShot(0, [] {
        for (auto *widget : QApplication::topLevelWidgets()) {
            if (auto *box = qobject_cast<QMessageBox *>(widget)) { box->done(QMessageBox::Cancel); }
        }
    });
    window.m_projects->setCurrentRow(1);
    QCOMPARE(window.m_currentProject, original);
    QCOMPARE(window.m_projects->currentItem()->text(), original);
    QVERIFY(window.m_dirty);
}

void EditorUiTests::staleDocumentResponsesAreIgnored()
{
    MainWindow window(nullptr, false);
    window.m_remoteMode = true;
    window.m_currentProject = QStringLiteral("Beta");
    window.m_pendingOpenOperation = QStringLiteral("document:Beta:app.json");
    const QJsonObject payload{{QStringLiteral("path"), QStringLiteral("app.json")}, {QStringLiteral("content"), QStringLiteral("{\"name\":\"Beta\"}")}};
    window.handleApiJson(QStringLiteral("document:Alpha:app.json"), payload);
    QVERIFY(window.m_currentDocument.isEmpty());
    window.handleApiJson(QStringLiteral("document:Beta:app.json"), payload);
    QCOMPARE(window.m_currentDocument, QStringLiteral("app.json"));
    window.m_codeEditor->setPlainText(QStringLiteral("{\"unsaved\":true}"));
    window.m_pendingOpenOperation = QStringLiteral("document:Beta:config/new.json");
    window.handleApiJson(window.m_pendingOpenOperation, payload);
    QVERIFY(window.m_codeEditor->toPlainText().contains(QStringLiteral("unsaved")));
    QVERIFY(window.m_dirty);
}

void EditorUiTests::workerRefreshRespectsPermissionsAndGlobalScope()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    TeamWorkspace team(&api);
    team.setCapabilities(capabilities({QStringLiteral("profiles.read"), QStringLiteral("members.read"), QStringLiteral("areas.read"), QStringLiteral("messages.read")}));
    team.refreshAll();
    QVERIFY(waitUntil([&] { return (server.requests.size()) == (3); }));
    QCOMPARE(server.requests.size(), 3);
    bool requestedGlobalSpaces = false;
    for (const auto &request : server.requests) {
        QVERIFY(!request.contains("/roles "));
        QVERIFY(!request.contains("/audit?"));
        requestedGlobalSpaces = requestedGlobalSpaces || request.contains("/areas?project=*");
    }
    QVERIFY(requestedGlobalSpaces);
    auto *composer = team.findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    QVERIFY(!composer->isEnabled());
}

void EditorUiTests::failedMessagePreservesDraftAndPreventsDuplicatePosts()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    int posts = 0;
    server.respond = [&posts](const QByteArray &request) {
        if (request.startsWith("POST ") && request.contains("/messages ")) {
            ++posts;
            return posts == 1 ? WorkspaceServer::Response{QByteArray(R"({"detail":"Temporary failure"})"), QByteArray("503 Service Unavailable"), 60}
                              : WorkspaceServer::Response{QByteArray(R"({"id":"saved"})"), QByteArray("201 Created"), 60};
        }
        return WorkspaceServer::Response{};
    };
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    TeamWorkspace team(&api);
    team.setCapabilities(capabilities({QStringLiteral("areas.read"), QStringLiteral("messages.read"), QStringLiteral("messages.write")}));
    deliver(api, QStringLiteral("team-areas:*"), spaces());
    auto *composer = team.findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    composer->setPlainText(QStringLiteral("Keep this draft"));
    QSignalSpy failed(&api, &ApiClient::requestFailed);
    QSignalSpy acknowledged(&api, &ApiClient::jsonReceived);
    QVERIFY(QMetaObject::invokeMethod(&team, "sendMessage"));
    QVERIFY(QMetaObject::invokeMethod(&team, "sendMessage"));
    QVERIFY(waitUntil([&] { return (failed.size()) == (1); }));
    QCOMPARE(failed.size(), 1);
    QCOMPARE(posts, 1);
    QCOMPARE(composer->toPlainText(), QStringLiteral("Keep this draft"));
    QVERIFY(QMetaObject::invokeMethod(&team, "sendMessage"));
    composer->setPlainText(QStringLiteral("Newer unsent text"));
    QVERIFY(waitUntil([&] {
        for (const auto &record : acknowledged) {
            if (record.at(0).toString().startsWith(QStringLiteral("team-message:"))) { return true; }
        }
        return false;
    }));
    QCOMPARE(posts, 2);
    QCOMPARE(composer->toPlainText(), QStringLiteral("Newer unsent text"));
    QVERIFY(QMetaObject::invokeMethod(&team, "sendMessage"));
    QVERIFY(waitUntil([&] { return composer->toPlainText().isEmpty(); }));
    QCOMPARE(posts, 3);
    QVERIFY(composer->toPlainText().isEmpty());
}

void EditorUiTests::workspaceIgnoresStaleResponsesAndKeepsAreaDrafts()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    TeamWorkspace team(&api);
    team.setCapabilities(capabilities({QStringLiteral("areas.read"), QStringLiteral("messages.write")}));
    team.setProject(QStringLiteral("Alpha"));
    deliver(api, QStringLiteral("team-areas:Alpha"), spaces());
    auto *areas = team.findChild<QListWidget *>(QStringLiteral("areaList"));
    auto *composer = team.findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    auto *messageList = team.findChild<ChatView *>(QStringLiteral("messageList"));
    composer->setPlainText(QStringLiteral("Alpha draft"));
    areas->setCurrentRow(1);
    composer->setPlainText(QStringLiteral("Beta draft"));
    deliver(api, QStringLiteral("team-messages:alpha"), messages(QStringLiteral("wrong area")));
    QCOMPARE(messageList->messageCount(), 0);
    deliver(api, QStringLiteral("team-messages:beta"), messages(QStringLiteral("correct area")));
    QCOMPARE(messageList->messageAt(0).value(QStringLiteral("body")).toString(), QStringLiteral("correct area"));
    areas->setCurrentRow(0);
    QCOMPARE(composer->toPlainText(), QStringLiteral("Alpha draft"));
    deliver(api, QStringLiteral("team-areas:Alpha"), spaces());
    QCOMPARE(composer->toPlainText(), QStringLiteral("Alpha draft"));
    team.setProject(QStringLiteral("Beta"));
    deliver(api, QStringLiteral("team-areas:Alpha"), spaces());
    QCOMPARE(areas->count(), 0);
    deliver(api, QStringLiteral("team-notes:Alpha"), QJsonObject{{QStringLiteral("notes"), QJsonArray{QJsonObject{{QStringLiteral("title"), QStringLiteral("wrong project")}}}}});
    QCOMPARE(team.findChild<QTreeWidget *>(QStringLiteral("noteList"))->topLevelItemCount(), 0);
    deliver(api, QStringLiteral("team-attachments:alpha"), QJsonObject{{QStringLiteral("attachments"), QJsonArray{QJsonObject{}}}});
    QCOMPARE(team.findChild<QTreeWidget *>(QStringLiteral("attachmentList"))->topLevelItemCount(), 0);
}

void EditorUiTests::noteAcknowledgementPreservesNewerEdits()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    server.respond = [](const QByteArray &request) {
        return WorkspaceServer::Response{QByteArray("{}"), QByteArray("200 OK"), request.startsWith("POST ") ? 60 : 0};
    };
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    TeamWorkspace team(&api);
    team.setCapabilities(capabilities({QStringLiteral("notes.write")}));
    auto *title = team.findChild<QLineEdit *>(QStringLiteral("noteTitle"));
    auto *body = team.findChild<QTextEdit *>(QStringLiteral("noteBody"));
    title->setText(QStringLiteral("First title"));
    body->setPlainText(QStringLiteral("Submitted body"));
    QVERIFY(QMetaObject::invokeMethod(&team, "saveNote"));
    title->setText(QStringLiteral("Newer title"));
    body->setPlainText(QStringLiteral("Newer body"));
    QTest::qWait(150);
    QCOMPARE(title->text(), QStringLiteral("Newer title"));
    QCOMPARE(body->toPlainText(), QStringLiteral("Newer body"));
}

void EditorUiTests::resetClearsSessionData()
{
    ApiClient api;
    TeamWorkspace team(&api);
    team.findChild<ChatComposer *>(QStringLiteral("messageComposer"))->setPlainText(QStringLiteral("private message"));
    team.findChild<QLineEdit *>(QStringLiteral("noteTitle"))->setText(QStringLiteral("private note"));
    team.findChild<QTextEdit *>(QStringLiteral("noteBody"))->setPlainText(QStringLiteral("private body"));
    auto *rows = team.findChild<QTableWidget *>(QStringLiteral("databaseRows"));
    rows->setRowCount(5);
    rows->setColumnCount(4);
    team.reset();
    QVERIFY(team.findChild<ChatComposer *>(QStringLiteral("messageComposer"))->toPlainText().isEmpty());
    QVERIFY(team.findChild<QLineEdit *>(QStringLiteral("noteTitle"))->text().isEmpty());
    QVERIFY(team.findChild<QTextEdit *>(QStringLiteral("noteBody"))->toPlainText().isEmpty());
    QCOMPARE(rows->rowCount(), 0);
    QCOMPARE(rows->columnCount(), 0);
}


void EditorUiTests::founderWildcardPermissions()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    TeamWorkspace team(&api);
    team.setCapabilities(capabilities({QStringLiteral("*")}));
    team.refreshAll();
    QVERIFY(waitUntil([&] { return (server.requests.size()) == (6); }));
    QCOMPARE(server.requests.size(), 6);
    bool roles = false;
    bool audit = false;
    for (const auto &request : server.requests) {
        roles = roles || request.contains("/roles ");
        audit = audit || request.contains("/audit?");
    }
    QVERIFY(roles && audit);
    deliver(api, QStringLiteral("team-areas:*"), spaces());
    QVERIFY(team.findChild<ChatComposer *>(QStringLiteral("messageComposer"))->isEnabled());
}

void EditorUiTests::restrictedNotesCarryReaderRank()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost, 0));
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    TeamWorkspace team(&api);
    team.setCapabilities(capabilities({QStringLiteral("notes.write")}));
    team.findChild<QLineEdit *>(QStringLiteral("noteTitle"))->setText(QStringLiteral("Restricted note"));
    team.findChild<QComboBox *>(QStringLiteral("noteVisibility"))->setCurrentText(QStringLiteral("restricted"));
    auto *rank = team.findChild<QSpinBox *>(QStringLiteral("noteMinimumRank"));
    QCOMPARE(rank->value(), 100);
    rank->setValue(60);
    QVERIFY(QMetaObject::invokeMethod(&team, "saveNote"));
    QVERIFY(waitUntil([&] { return (server.requests.size()) == (1); }));
    QCOMPARE(server.requests.size(), 1);
    const auto request = server.requests.first();
    const auto body = QJsonDocument::fromJson(request.mid(request.indexOf("\r\n\r\n") + 4)).object();
    QCOMPARE(body.value(QStringLiteral("minimum_rank")).toInt(), 60);
    QCOMPARE(body.value(QStringLiteral("visibility")).toString(), QStringLiteral("restricted"));
}

void EditorUiTests::conversationKeepsSelectionAndEscapesContent()
{
    ChatView view;
    QVERIFY(view.setConversation({}, {}));
    QVERIFY(view.toPlainText().contains(QStringLiteral("beginning")));
    const QJsonArray records{QJsonObject{{QStringLiteral("body"), QStringLiteral("<img src='https://evil.test/pixel'>\nhello")},
                                        {QStringLiteral("display_name"), QStringLiteral("<Founder>")}}};
    QVERIFY(view.setConversation(records, {}));
    QVERIFY(view.toPlainText().contains(QStringLiteral("<Founder>")));
    QVERIFY(view.toPlainText().contains(QStringLiteral("<img src=")));
    auto cursor = view.textCursor();
    cursor.movePosition(QTextCursor::Start);
    cursor.movePosition(QTextCursor::Right, QTextCursor::KeepAnchor, 8);
    view.setTextCursor(cursor);
    const auto selected = view.textCursor().selectedText();
    QVERIFY(!view.setConversation(records, {}));
    QCOMPARE(view.textCursor().selectedText(), selected);
    QSignalSpy links(&view, &ChatView::attachmentRequested);
    view.anchorClicked(QUrl(QStringLiteral("forge-file:test-id")));
    QCOMPARE(links.size(), 1);
    QCOMPARE(links.first().first().toString(), QStringLiteral("test-id"));
    view.clearConversation();
    QVERIFY(view.setConversation({}, {}));
}

void EditorUiTests::composerSupportsMultilineAndFilePaste()
{
    ChatComposer composer;
    QSignalSpy send(&composer, &ChatComposer::sendRequested);
    QSignalSpy files(&composer, &ChatComposer::fileDropped);
    composer.setPlainText(QStringLiteral("first"));
    composer.moveCursor(QTextCursor::End);
    QTest::keyClick(&composer, Qt::Key_Return, Qt::ShiftModifier);
    QTest::keyClicks(&composer, QStringLiteral("second"));
    QCOMPARE(composer.toPlainText(), QStringLiteral("first\nsecond"));
    QCOMPARE(send.size(), 0);
    QTest::keyClick(&composer, Qt::Key_Return);
    QCOMPARE(send.size(), 1);
    composer.setPlainText(QString(8100, u'a'));
    QCOMPARE(composer.toPlainText().size(), 8000);
    QVERIFY(files.isEmpty());
}

void EditorUiTests::backgroundRefreshAndDownloadIntegrity()
{
    WorkspaceServer server;
    QVERIFY(server.listen(QHostAddress::LocalHost));
    server.respond = [](const QByteArray &request) {
        return WorkspaceServer::Response{request.startsWith("GET /__forge/editor/v1/attachments/")
            ? QByteArray("downloaded content") : QByteArray("{\"messages\":[],\"attachments\":[],\"calls\":[]}"), QByteArray("200 OK"), 20};
    };
    ApiClient api;
    QString error;
    QVERIFY(api.configure(server.url(), TestSession, true, &error));
    QSignalSpy activity(&api, &ApiClient::connectionActivityChanged);
    QSignalSpy received(&api, &ApiClient::jsonReceived);
    QSignalSpy failures(&api, &ApiClient::requestFailed);
    QSignalSpy downloaded(&api, &ApiClient::fileDownloaded);
    api.fetchMessages(QStringLiteral("alpha"));
    api.fetchAttachments(QStringLiteral("alpha"));
    api.fetchCalls(QStringLiteral("alpha"));
    QVERIFY(waitUntil([&] { return received.size() == 3; }));
    QVERIFY(activity.isEmpty());
    QTemporaryDir directory;
    const auto path = directory.filePath(QStringLiteral("existing.txt"));
    QFile file(path);
    QVERIFY(file.open(QIODevice::WriteOnly));
    file.write("keep original"); file.close();
    api.downloadAttachment(QStringLiteral("test"), path, 1024, QString(64, u'0'));
    QVERIFY(waitUntil([&] { return !failures.isEmpty(); }));
    QVERIFY(downloaded.isEmpty());
    QVERIFY(file.open(QIODevice::ReadOnly));
    QCOMPARE(file.readAll(), QByteArray("keep original")); file.close();
    failures.clear();
    api.downloadAttachment(QStringLiteral("test"), path, 1024,
        QString::fromLatin1(QCryptographicHash::hash(QByteArray("downloaded content"), QCryptographicHash::Sha256).toHex()));
    QVERIFY(waitUntil([&] { return downloaded.size() == 1; }));
    QVERIFY(file.open(QIODevice::ReadOnly));
    QCOMPARE(file.readAll(), QByteArray("downloaded content"));
    QVERIFY(failures.isEmpty());
}

void EditorUiTests::liveServerContract()
{
    const auto serverUrl = qEnvironmentVariable("FORGE_EDITOR_TEST_SERVER_URL");
    if (serverUrl.isEmpty()) { QSKIP("Run run_server_contract.py to test the canonical main-branch server."); }
    ApiClient api;
    QSignalSpy received(&api, &ApiClient::jsonReceived);
    QSignalSpy failures(&api, &ApiClient::requestFailed);
    QSignalSpy downloads(&api, &ApiClient::fileDownloaded);
    QJsonObject payload;
    const auto awaitPayload = [&received, &payload](const QString &operation) {
        QElapsedTimer deadline;
        deadline.start();
        while (deadline.elapsed() < 10'000) {
            for (qsizetype index = 0; index < received.size(); ++index) {
                if (received.at(index).at(0).toString() == operation) {
                    payload = received.takeAt(index).at(1).toJsonObject();
                    return true;
                }
            }
            QTest::qWait(10);
        }
        return false;
    };
    QString error;
    QVERIFY2(api.configureServer(QUrl(serverUrl), true, &error), qPrintable(error));
    api.setupFounder(QByteArray("forge-ui-test-setup-0123456789abcdefghijklmnopqrstuv"), QStringLiteral("ui.founder"),
                     QStringLiteral("Granite river orbits seven moons 42!"), QStringLiteral("UI Founder"));
    QVERIFY(awaitPayload(QStringLiteral("auth-setup")));
    QVERIFY(api.isConfigured());
    api.fetchCapabilities();
    QVERIFY(awaitPayload(QStringLiteral("capabilities")));
    const auto founderCapabilities = payload;
    QVERIFY(founderCapabilities.value(QStringLiteral("collaboration")).toBool());
    api.fetchProjects();
    QVERIFY(awaitPayload(QStringLiteral("projects")));
    QCOMPARE(payload.value(QStringLiteral("projects")).toArray().first().toObject().value(QStringLiteral("directory")).toString(), QStringLiteral("Workspace"));
    api.fetchDocuments(QStringLiteral("Workspace"));
    QVERIFY(awaitPayload(QStringLiteral("documents:Workspace")));
    QVERIFY(payload.value(QStringLiteral("documents")).toArray().size() >= 2);
    api.fetchDocument(QStringLiteral("Workspace"), QStringLiteral("config/40-resources.json"));
    QVERIFY(awaitPayload(QStringLiteral("document:Workspace:config/40-resources.json")));
    const auto sha = payload.value(QStringLiteral("sha256")).toString();
    api.saveDocument(QStringLiteral("Workspace"), QStringLiteral("config/40-resources.json"), QByteArray("{\"resources\":[]}\n"), sha);
    QVERIFY(awaitPayload(QStringLiteral("save:Workspace:config/40-resources.json")));
    api.validateProject(QStringLiteral("Workspace"));
    QVERIFY(awaitPayload(QStringLiteral("validate:Workspace")));
    api.updateProfile(QJsonObject{{QStringLiteral("status"), QStringLiteral("Online")}});
    QVERIFY(awaitPayload(QStringLiteral("team-profile-update")));
    QCOMPARE(payload.value(QStringLiteral("status")).toString(), QStringLiteral("Online"));
    api.createArea(QStringLiteral("Workspace"), QStringLiteral("Team chat"), QStringLiteral("Native server contract"), QStringLiteral("open"), 0);
    QVERIFY(awaitPayload(QStringLiteral("team-area-create:Workspace")));
    const auto area = payload.value(QStringLiteral("id")).toString();
    QVERIFY(!area.isEmpty());
    const auto body = QStringLiteral("Forge sohbet — merhaba 👋");
    api.postMessage(area, body);
    QVERIFY(awaitPayload(QStringLiteral("team-message:%1").arg(area)));
    api.fetchMessages(area);
    QVERIFY(awaitPayload(QStringLiteral("team-messages:%1").arg(area)));
    QCOMPARE(payload.value(QStringLiteral("messages")).toArray().last().toObject().value(QStringLiteral("body")).toString(), body);
    api.createNote(QStringLiteral("Workspace"), area, QStringLiteral("Contract note"), body, QStringLiteral("private"));
    QVERIFY(awaitPayload(QStringLiteral("team-note-create:Workspace")));
    api.fetchNotes(QStringLiteral("Workspace"));
    QVERIFY(awaitPayload(QStringLiteral("team-notes:Workspace")));
    QCOMPARE(payload.value(QStringLiteral("notes")).toArray().first().toObject().value(QStringLiteral("body")).toString(), body);
    QTemporaryDir directory;
    QVERIFY(directory.isValid());
    const auto source = directory.filePath(QStringLiteral("Türkçe paylaşım 👋.txt"));
    QFile file(source);
    QVERIFY(file.open(QIODevice::WriteOnly));
    file.write(body.toUtf8());
    file.close();
    api.uploadAttachment(area, source, 1024 * 1024);
    QVERIFY(awaitPayload(QStringLiteral("team-attachment-upload:%1").arg(area)));
    const auto attachmentId = payload.value(QStringLiteral("id")).toString();
    QCOMPARE(payload.value(QStringLiteral("original_name")).toString(), QFileInfo(source).fileName());
    const auto attachmentSha = payload.value(QStringLiteral("sha256")).toString();
    api.fetchAttachments(area);
    QVERIFY(awaitPayload(QStringLiteral("team-attachments:%1").arg(area)));
    QCOMPARE(payload.value(QStringLiteral("attachments")).toArray().size(), 1);
    const auto target = directory.filePath(QStringLiteral("downloaded.txt"));
    api.downloadAttachment(attachmentId, target, 1024 * 1024, attachmentSha);
    QVERIFY(waitUntil([&] { return (downloads.size()) == (1); }));
    QCOMPARE(downloads.size(), 1);
    QFile downloaded(target);
    QVERIFY(downloaded.open(QIODevice::ReadOnly));
    QCOMPARE(downloaded.readAll(), body.toUtf8());
    api.fetchDatabases(QStringLiteral("Workspace"));
    QVERIFY(awaitPayload(QStringLiteral("team-databases:Workspace")));
    QCOMPARE(payload.value(QStringLiteral("databases")).toArray().first().toObject().value(QStringLiteral("alias")).toString(), QStringLiteral("primary"));
    api.fetchDatabaseRows(QStringLiteral("Workspace"), QStringLiteral("primary"), QStringLiteral("items"));
    QVERIFY(awaitPayload(QStringLiteral("team-rows:Workspace:primary:items")));
    const auto row = payload.value(QStringLiteral("rows")).toArray().first().toObject();
    QCOMPARE(row.value(QStringLiteral("name")).toString(), QStringLiteral("Native contract"));
    QVERIFY(!row.contains(QStringLiteral("secret")));
    api.startCall(area, QStringLiteral("audio"));
    QVERIFY(awaitPayload(QStringLiteral("team-call:%1").arg(area)));
    const auto call = payload.value(QStringLiteral("id")).toString();
    api.fetchCalls(area);
    QVERIFY(awaitPayload(QStringLiteral("team-calls:%1").arg(area)));
    QCOMPARE(payload.value(QStringLiteral("calls")).toArray().first().toObject().value(QStringLiteral("id")).toString(), call);
    api.createCallTicket(call);
    QVERIFY(awaitPayload(QStringLiteral("team-call-ticket:%1").arg(call)));
    QVERIFY(api.callClientUrl(payload.value(QStringLiteral("call_client_path")).toString(), payload.value(QStringLiteral("ticket")).toString()).isValid());
    api.fetchAudit(QStringLiteral("Workspace"));
    QVERIFY(awaitPayload(QStringLiteral("team-audit:Workspace")));
    QVERIFY(!payload.value(QStringLiteral("events")).toArray().isEmpty());
    api.createInvitation(QStringLiteral("00000000-0000-0000-0000-000000000003"), QStringLiteral("Workspace"), 1);
    QVERIFY(awaitPayload(QStringLiteral("team-invitation")));
    const auto invitation = payload.value(QStringLiteral("invitation")).toString();
    ApiClient worker;
    QSignalSpy workerReceived(&worker, &ApiClient::jsonReceived);
    QSignalSpy workerFailures(&worker, &ApiClient::requestFailed);
    QVERIFY(worker.configureServer(QUrl(serverUrl), true, &error));
    worker.registerMember(invitation, QStringLiteral("ui.worker"), QStringLiteral("Copper falcon maps quiet valleys 84!"), QStringLiteral("UI Worker"));
    QVERIFY(waitUntil([&] { return !workerReceived.isEmpty() || !workerFailures.isEmpty(); }));
    QVERIFY2(workerFailures.isEmpty(), workerFailures.isEmpty() ? "" : qPrintable(workerFailures.first().at(2).toString()));
    QVERIFY(worker.isConfigured());
    workerReceived.clear();
    worker.fetchCapabilities(QStringLiteral("Workspace"));
    QVERIFY(waitUntil([&] { return !workerReceived.isEmpty() || !workerFailures.isEmpty(); }));
    QVERIFY2(workerFailures.isEmpty(), workerFailures.isEmpty() ? "" : qPrintable(workerFailures.first().at(2).toString()));
    const auto workerCapabilities = workerReceived.first().at(1).toJsonObject();
    QVERIFY(!workerCapabilities.value(QStringLiteral("permissions")).toArray().contains(QStringLiteral("roles.read")));
    TeamWorkspace team(&worker);
    team.setProject(QStringLiteral("Workspace"));
    team.setCapabilities(workerCapabilities);
    team.refreshAll();
    auto *areas = team.findChild<QListWidget *>(QStringLiteral("areaList"));
    QVERIFY(waitUntil([&] { return areas->count() > 0; }));
    auto *composer = team.findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    QVERIFY(composer->isEnabled());
    composer->setPlainText(QStringLiteral("Worker reply"));
    QVERIFY(QMetaObject::invokeMethod(&team, "sendMessage"));
    QVERIFY(waitUntil([&] { return composer->toPlainText().isEmpty(); }));
    auto *messageList = team.findChild<ChatView *>(QStringLiteral("messageList"));
    QVERIFY(waitUntil([&] { return (messageList->messageCount()) == (2); }));
    QCOMPARE(messageList->messageCount(), 2);
    QCOMPARE(messageList->messageAt(1).value(QStringLiteral("body")).toString(), QStringLiteral("Worker reply"));
    QTest::qWait(100);
    QCOMPARE(workerFailures.size(), 0);
    QCOMPARE(failures.size(), 0);
    MainWindow window(nullptr, false);
    window.m_remoteMode = true;
    QVERIFY(window.m_api->configureServer(QUrl(serverUrl), true, &error));
    QSignalSpy windowFailures(window.m_api, &ApiClient::requestFailed);
    window.m_api->login(QStringLiteral("ui.worker"), QStringLiteral("Copper falcon maps quiet valleys 84!"));
    QVERIFY(waitUntil([&] { return (window.m_currentProject) == (QStringLiteral("Workspace")); }));
    QCOMPARE(window.m_currentProject, QStringLiteral("Workspace"));
    auto *windowAreas = window.m_teamWorkspace->findChild<QListWidget *>(QStringLiteral("areaList"));
    QVERIFY(waitUntil([&] { return windowAreas->count() > 0; }));
    QVERIFY(window.m_teamWorkspace->findChild<ChatComposer *>(QStringLiteral("messageComposer"))->isEnabled());
    QTest::qWait(100);
    QCOMPARE(windowFailures.size(), 0);
    const auto screenshot = qEnvironmentVariable("FORGE_EDITOR_TEST_SCREENSHOT");
    if (!screenshot.isEmpty()) {
        window.resize(1024, 640);
        window.show();
        QTest::qWait(300);
        QVERIFY(window.grab().save(screenshot));
        auto *header = window.m_disconnectButton->parentWidget();
        QVERIFY(header->rect().contains(window.m_disconnectButton->geometry()));
        QVERIFY(header->rect().contains(window.m_connectionLabel->geometry()));
        QVERIFY(!window.m_disconnectButton->geometry().intersects(window.m_connectionLabel->geometry()));
    }
    MainWindow founderWindow(nullptr, false);
    founderWindow.m_remoteMode = true;
    QVERIFY(founderWindow.m_api->configureServer(QUrl(serverUrl), true, &error));
    QSignalSpy founderWindowFailures(founderWindow.m_api, &ApiClient::requestFailed);
    founderWindow.m_api->login(QStringLiteral("ui.founder"), QStringLiteral("Granite river orbits seven moons 42!"));
    auto *founderComposer = founderWindow.m_teamWorkspace->findChild<ChatComposer *>(QStringLiteral("messageComposer"));
    QVERIFY(waitUntil([&] { return founderComposer->isEnabled(); }));
    QCOMPARE(founderWindow.m_currentProject, QStringLiteral("Workspace"));
    for (auto *button : founderWindow.m_teamWorkspace->findChildren<QPushButton *>()) {
        if (button->property("forgePermission") == QStringLiteral("calls.start")) { QVERIFY(button->isEnabled()); }
    }
    founderWindow.resize(1024, 640);
    founderWindow.show();
    QTest::qWait(60);
    auto *founderMessages = founderWindow.m_teamWorkspace->findChild<ChatView *>(QStringLiteral("messageList"));
    auto *founderAreas = founderWindow.m_teamWorkspace->findChild<QListWidget *>(QStringLiteral("areaList"));
    QVERIFY2(founderMessages->viewport()->height() >= 75, "Compact dock must show conversation rows, not just its header");
    QVERIFY2(founderAreas->viewport()->height() >= 35, "Compact dock must show the selected space");
    QVERIFY(founderWindow.rect().contains(QRect(founderComposer->mapTo(&founderWindow, QPoint(0, 0)), founderComposer->size())));
    founderComposer->setPlainText(QStringLiteral("Founder UI reply"));
    QTest::keyClick(founderComposer, Qt::Key_Return);
    QVERIFY(waitUntil([&] { return founderComposer->toPlainText().isEmpty(); }));
    QVERIFY(waitUntil([&] { return founderWindow.m_teamWorkspace->findChild<ChatView *>(QStringLiteral("messageList"))->messageCount() == 3; }));
    // Exercise the actual chat file drop -> background snapshot -> multipart -> server acknowledgement flow.
    founderMessages->fileDropped(source);
    QVERIFY(waitUntil([&] { return founderWindow.m_teamWorkspace->findChild<QLabel *>(QStringLiteral("transferStatus"))->text().contains(QStringLiteral("successfully")); }, 10000));
    QVERIFY(waitUntil([&] { return founderWindow.m_teamWorkspace->findChild<QTreeWidget *>(QStringLiteral("attachmentList"))->topLevelItemCount() == 2; }));
    QCOMPARE(founderWindowFailures.size(), 0);
    if (!screenshot.isEmpty()) { QVERIFY(founderWindow.grab().save(screenshot + QStringLiteral(".founder.png"))); }
    founderWindow.m_api->logout();
    window.m_api->logout();
    worker.logout();
    api.logout();
    QVERIFY(awaitPayload(QStringLiteral("auth-logout")));
}

QTEST_MAIN(EditorUiTests)
#include "tst_editor_ui.moc"
