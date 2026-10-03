#include "MainWindow.hpp"

#include "CodeEditor.hpp"
#include "ConnectionDialog.hpp"
#include "DocumentCodec.hpp"
#include "EditorSettings.hpp"
#include "JsonHighlighter.hpp"
#include "NodeGraphEditor.hpp"
#include "PluginCatalogClient.hpp"
#include "PythonSdkPanel.hpp"
#include "TeamWorkspace.hpp"
#include "TemplateManager.hpp"
#include "UiSizing.hpp"
#include "VisualDesigner.hpp"

#include <QAction>
#include <QApplication>
#include <QCheckBox>
#include <QClipboard>
#include <QCloseEvent>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDockWidget>
#include <QDir>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFontDatabase>
#include <QFormLayout>
#include <QGraphicsOpacityEffect>
#include <QGuiApplication>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QJsonArray>
#include <QJsonDocument>
#include <QIcon>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMenuBar>
#include <QMessageBox>
#include <QProgressBar>
#include <QPropertyAnimation>
#include <QVariantAnimation>
#include <QPixmap>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSettings>
#include <QShortcut>
#include <QSignalBlocker>
#include <QSplitter>
#include <QStackedWidget>
#include <QStandardPaths>
#include <QStatusBar>
#include <QStyle>
#include <QRegularExpression>
#include <QResizeEvent>
#include <QScreen>
#include <QScopedValueRollback>
#include <QScrollArea>
#include <QSizePolicy>
#include <QTextStream>
#include <QTimer>
#include <QToolBar>
#include <QToolButton>
#include <QTreeWidget>
#include <QVBoxLayout>

namespace {
constexpr auto ProjectPathRole = Qt::UserRole + 1;
constexpr auto ProjectSourceRole = Qt::UserRole + 2;
constexpr auto DocumentPathRole = Qt::UserRole + 3;
constexpr int LocalSource = 1;
constexpr int RemoteSource = 2;

QLabel *eyebrow(const QString &text, QWidget *parent)
{
    auto *label = new QLabel(text, parent);
    label->setObjectName(QStringLiteral("panelEyebrow"));
    return label;
}

QString currentModeName(bool remote)
{
    return remote ? QStringLiteral("REMOTE") : QStringLiteral("LOCAL");
}
} // namespace

MainWindow::MainWindow(QWidget *parent, bool restoreLayout)
    : QMainWindow(parent)
    , m_api(new ApiClient(this))
    , m_persistWindowLayout(restoreLayout)
{
    QCoreApplication::setOrganizationName(QStringLiteral("Cavanshirpro"));
    QCoreApplication::setApplicationName(QStringLiteral("JSON API Forge Editor"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.5.2"));
    m_preferences = EditorPreferences::load();
    applyPreferences();
    setWindowTitle(QStringLiteral("JSON API Forge Editor v0.5.2"));
    setWindowIcon(QIcon(QStringLiteral(":/branding/logo.png")));
    setMinimumSize(720, 480);
    resize(ForgeEditorUi::boundedSize(this, QSize(1480, 900), minimumSize()));
    buildInterface();
    buildActions();
    applyStyle();
    applyPreferences();
    if (restoreLayout && m_preferences.restoreWindowLayout) {
        restoreWindowLayout();
    }
    showWelcome();
    updateConnectionActions();

    m_userPluginDirectory = QDir(QStandardPaths::writableLocation(QStandardPaths::AppLocalDataLocation))
                                .filePath(QStringLiteral("plugins"));
    const QStringList pluginDirectories{
        QDir(QCoreApplication::applicationDirPath()).filePath(QStringLiteral("plugins")),
        m_userPluginDirectory,
    };
    m_pluginManager = new PluginManager(pluginDirectories);
    reloadPlugins();

    connect(m_api, &ApiClient::jsonReceived, this, &MainWindow::handleApiJson);
    connect(m_api, &ApiClient::requestFailedDetailed, this, &MainWindow::handleApiError);
    connect(m_api, &ApiClient::connectionActivityChanged, this, [this](bool active) { m_activity->setVisible(active); });
    connect(m_codeEditor, &QPlainTextEdit::textChanged, this, [this] {
        if (!m_updatingEditor && !m_currentDocument.isEmpty()) {
            setDirty(true);
        }
    });
    connect(m_visualDesigner, &VisualDesigner::documentChanged, this, [this] { setDirty(true); });
    connect(m_visualDesigner, &VisualDesigner::statusMessage, this,
            [this](const QString &message) { showStatusMessage(message); });
    connect(m_graphEditor, &NodeGraphEditor::documentChanged, this, [this] { setDirty(true); });
    connect(m_graphEditor, &NodeGraphEditor::statusMessage, this,
            [this](const QString &message) { showStatusMessage(message, 6000); });
    connect(m_pythonPanel, &PythonSdkPanel::statusMessage, this,
            [this](const QString &message) { showStatusMessage(message, 6000); });
    connect(m_teamWorkspace, &TeamWorkspace::statusMessage, this,
            [this](const QString &message) { showStatusMessage(message, 8000); });
    connect(m_teamWorkspace, &TeamWorkspace::retryConnectionRequested, this, [this] {
        if (!m_api->isConfigured()) { connectToServer(); return; }
        m_teamWorkspace->setCapabilities({});
        m_api->fetchCapabilities(m_currentProject);
        m_api->fetchProjects();
    });
}

MainWindow::~MainWindow()
{
    // Child models can emit selection/visibility signals during QWidget teardown,
    // before QObject disconnects receivers. Stop callbacks while our type is valid.
    for (auto *child : findChildren<QObject *>()) { QObject::disconnect(child, nullptr, this, nullptr); }
    if (m_api != nullptr) { m_api->cancelActiveRequests(); }
    if (m_pluginManager != nullptr) {
        m_pluginManager->unloadAll();
        delete m_pluginManager;
    }
}

void MainWindow::showVisualPreview()
{
    const QJsonObject document{
        {QStringLiteral("name"), QStringLiteral("Inventory API")},
        {QStringLiteral("resources"), QJsonArray{QJsonObject{
            {QStringLiteral("path"), QStringLiteral("products")}, {QStringLiteral("database"), QStringLiteral("primary")},
            {QStringLiteral("table"), QStringLiteral("products")}, {QStringLiteral("auto_create"), false},
            {QStringLiteral("columns"), QJsonObject{{QStringLiteral("name"), QStringLiteral("TEXT")}, {QStringLiteral("price"), QStringLiteral("REAL")}}},
            {QStringLiteral("allowed_actions"), QJsonArray{QStringLiteral("list"), QStringLiteral("read")}}}}},
        {QStringLiteral("data_sources"), QJsonArray{QJsonObject{
            {QStringLiteral("name"), QStringLiteral("store-info")}, {QStringLiteral("type"), QStringLiteral("static")},
            {QStringLiteral("public"), true}, {QStringLiteral("data"), QJsonObject{{QStringLiteral("currency"), QStringLiteral("USD")}}}}}}
    };
    loadDocument(QStringLiteral("config/inventory.json"), DocumentCodec::prettyJson(document), {});
    showVisualMode();
    auto *tree = m_visualDesigner->findChild<QTreeWidget *>(QStringLiteral("designerCanvas"));
    for (int index = 0; index < tree->topLevelItemCount(); ++index) {
        if (tree->topLevelItem(index)->text(0) == QStringLiteral("resources")) {
            tree->setCurrentItem(tree->topLevelItem(index)->child(0)); break;
        }
    }
}

void MainWindow::showGraphPreview()
{
    m_remoteMode = false;
    m_currentProject = QStringLiteral("GraphPreview");
    const auto target = QStringLiteral("config/50-preview-operation.json");
    loadDocument(QStringLiteral("graphs/preview.forgegraph.json"),
                 DocumentCodec::prettyJson(NodeGraphEditor::starterDocument(target)), QStringLiteral("new"));
    setDirty(false);
}

void MainWindow::showTeamPreview()
{
    m_teamDock->show();
    m_teamDock->raise();
    arrangeTeamDock();
}

void MainWindow::buildInterface()
{
    auto *root = new QSplitter(this);
    m_rootSplitter = root;
    root->setObjectName(QStringLiteral("rootSplitter"));
    root->setHandleWidth(1);
    root->setChildrenCollapsible(false);
    setCentralWidget(root);

    auto *sidebarScroll = new QScrollArea(root);
    sidebarScroll->setWidgetResizable(true);
    sidebarScroll->setFrameShape(QFrame::NoFrame);
    m_sidebar = sidebarScroll;
    auto *sidebarContents = new QWidget(sidebarScroll);
    sidebarContents->setObjectName(QStringLiteral("sidebarContents"));
    sidebarScroll->setWidget(sidebarContents);
    m_sidebar->setObjectName(QStringLiteral("sidebar"));
    m_sidebar->setMinimumWidth(220);
    m_sidebar->setMaximumWidth(320);
    auto *sideLayout = new QVBoxLayout(sidebarContents);
    sideLayout->setContentsMargins(18, 20, 18, 16);
    sideLayout->setSpacing(12);
    auto *brandRow = new QWidget(m_sidebar);
    auto *brandLayout = new QHBoxLayout(brandRow);
    brandLayout->setContentsMargins(0, 0, 0, 0);
    auto *logo = new QLabel(brandRow);
    logo->setPixmap(QPixmap(QStringLiteral(":/branding/mark.png"))
                        .scaled(48, 48, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    auto *brandText = new QLabel(QStringLiteral("JSON API\nFORGE"), brandRow);
    brandText->setObjectName(QStringLiteral("brandText"));
    brandLayout->addWidget(logo);
    brandLayout->addWidget(brandText);
    brandLayout->addStretch();
    sideLayout->addWidget(brandRow);
    auto *version = new QLabel(QStringLiteral("v0.5.2"), m_sidebar);
    version->setObjectName(QStringLiteral("editorVersion"));
    sideLayout->addWidget(version);
    sideLayout->addWidget(eyebrow(QStringLiteral("PROJECTS"), m_sidebar));
    m_projects = new QListWidget(m_sidebar);
    m_projects->setObjectName(QStringLiteral("projectList"));
    m_projects->setSpacing(3);
    sideLayout->addWidget(m_projects, 2);
    sideLayout->addWidget(eyebrow(QStringLiteral("DOCUMENTS"), m_sidebar));
    m_documents = new QTreeWidget(m_sidebar);
    m_documents->setObjectName(QStringLiteral("documentTree"));
    m_documents->setHeaderHidden(true);
    m_documents->setRootIsDecorated(true);
    sideLayout->addWidget(m_documents, 3);
    sideLayout->addWidget(eyebrow(QStringLiteral("SERVER POLICY"), m_sidebar));
    m_policyLabel = new QLabel(QStringLiteral("Connect to discover server-enforced capabilities."), m_sidebar);
    m_policyLabel->setObjectName(QStringLiteral("policyCard"));
    m_policyLabel->setWordWrap(true);
    sideLayout->addWidget(m_policyLabel);

    auto *content = new QWidget(root);
    content->setObjectName(QStringLiteral("contentArea"));
    auto *contentLayout = new QVBoxLayout(content);
    contentLayout->setContentsMargins(0, 0, 0, 0);
    contentLayout->setSpacing(0);
    auto *header = new QWidget(content);
    header->setObjectName(QStringLiteral("workspaceHeader"));
    auto *headerLayout = new QVBoxLayout(header);
    headerLayout->setContentsMargins(14, 8, 14, 8);
    headerLayout->setSpacing(6);
    auto *connectionRow = new QHBoxLayout;
    headerLayout->addLayout(connectionRow);
    auto *modeRow = new QHBoxLayout;
    headerLayout->addLayout(modeRow);
    header->setMinimumHeight(92);
    auto *sidebarButton = new QToolButton(header);
    m_sidebarButton = sidebarButton;
    sidebarButton->setCheckable(true);
    sidebarButton->setChecked(true);
    sidebarButton->setAccessibleName(QStringLiteral("Toggle sidebar"));
    sidebarButton->setText(QStringLiteral("☰"));
    sidebarButton->setToolTip(QStringLiteral("Toggle sidebar (Ctrl+\\)"));
    sidebarButton->setObjectName(QStringLiteral("iconButton"));
    connect(sidebarButton, &QToolButton::clicked, this, &MainWindow::toggleSidebar);
    connectionRow->addWidget(sidebarButton);
    m_breadcrumb = new QLabel(QStringLiteral("No document open"), header);
    m_breadcrumb->setObjectName(QStringLiteral("breadcrumb"));
    m_breadcrumb->setMinimumWidth(0);
    m_breadcrumb->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Preferred);
    m_breadcrumb->setToolTip(m_breadcrumb->text());
    connectionRow->addWidget(m_breadcrumb, 1);
    m_activity = new QProgressBar(header);
    m_activity->setRange(0, 0);
    m_activity->setFixedSize(72, 6);
    m_activity->setTextVisible(false);
    m_activity->hide();
    connectionRow->addWidget(m_activity);
    m_connectionLabel = new QLabel(QStringLiteral("●  Not connected"), header);
    m_connectionLabel->setObjectName(QStringLiteral("connectionOffline"));
    m_connectionLabel->setMaximumWidth(240);
    m_connectionLabel->setMinimumWidth(0);
    m_connectionLabel->setSizePolicy(QSizePolicy::Preferred, QSizePolicy::Preferred);
    m_disconnectButton = new QToolButton(header);
    m_disconnectButton->setObjectName(QStringLiteral("disconnectButton"));
    m_disconnectButton->setText(QStringLiteral("Disconnect"));
    m_disconnectButton->setToolTip(QStringLiteral("Disconnect from the Forge server"));
    m_disconnectButton->setAccessibleName(QStringLiteral("Disconnect from server"));
    m_disconnectButton->hide();
    connect(m_disconnectButton, &QToolButton::clicked, this, &MainWindow::disconnectServer);
    m_codeButton = new QToolButton(header);
    m_codeButton->setText(QStringLiteral("Code"));
    m_codeButton->setCheckable(true);
    m_codeButton->setEnabled(false);
    m_codeButton->setObjectName(QStringLiteral("modeButton"));
    m_codeButton->setMinimumWidth(76);
    m_visualButton = new QToolButton(header);
    m_visualButton->setText(QStringLiteral("Visual"));
    m_visualButton->setCheckable(true);
    m_visualButton->setEnabled(false);
    m_visualButton->setObjectName(QStringLiteral("modeButton"));
    m_visualButton->setMinimumWidth(76);
    m_graphButton = new QToolButton(header);
    m_graphButton->setText(QStringLiteral("Graph"));
    m_graphButton->setCheckable(true);
    m_graphButton->setEnabled(false);
    m_graphButton->setObjectName(QStringLiteral("modeButton"));
    m_graphButton->setMinimumWidth(76);
    connect(m_codeButton, &QToolButton::clicked, this, &MainWindow::showCodeMode);
    connect(m_visualButton, &QToolButton::clicked, this, &MainWindow::showVisualMode);
    connect(m_graphButton, &QToolButton::clicked, this, &MainWindow::showGraphMode);
    modeRow->addWidget(m_codeButton);
    modeRow->addWidget(m_visualButton);
    modeRow->addWidget(m_graphButton);
    modeRow->addStretch();
    connectionRow->addWidget(m_connectionLabel);
    connectionRow->addWidget(m_disconnectButton);
    contentLayout->addWidget(header);

    m_workspace = new QStackedWidget(content);
    m_workspace->setObjectName(QStringLiteral("workspaceStack"));
    // Hidden authoring pages must not force the bottom chat dock to give up
    // its message list on compact windows. The pages scroll independently.
    m_workspace->setMinimumHeight(0);
    m_workspace->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Ignored);
    auto *welcomeScroll = new QScrollArea(m_workspace);
    welcomeScroll->setWidgetResizable(true);
    welcomeScroll->setFrameShape(QFrame::NoFrame);
    m_welcomePage = welcomeScroll;
    auto *welcomeContents = new QWidget(welcomeScroll);
    welcomeContents->setObjectName(QStringLiteral("welcomePage"));
    welcomeScroll->setWidget(welcomeContents);
    m_welcomePage->setObjectName(QStringLiteral("welcomePage"));
    auto *welcomeLayout = new QVBoxLayout(welcomeContents);
    welcomeLayout->setContentsMargins(20, 20, 20, 28);
    welcomeLayout->setAlignment(Qt::AlignCenter);
    welcomeLayout->setSpacing(14);
    auto *welcomeLogo = new QLabel(m_welcomePage);
    welcomeLogo->setObjectName(QStringLiteral("welcomeLogo"));
    welcomeLogo->setPixmap(QPixmap(QStringLiteral(":/branding/mark.png"))
                               .scaled(170, 170, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    welcomeLogo->setAlignment(Qt::AlignCenter);
    auto *welcomeBadge = new QLabel(QStringLiteral("POLICY-AWARE  ·  LOCAL OR REMOTE"), m_welcomePage);
    welcomeBadge->setObjectName(QStringLiteral("welcomeBadge"));
    welcomeBadge->setAlignment(Qt::AlignCenter);
    welcomeBadge->setWordWrap(true);
    auto *welcomeTitle = new QLabel(QStringLiteral("Design APIs without losing the code."), m_welcomePage);
    welcomeTitle->setObjectName(QStringLiteral("welcomeTitle"));
    welcomeTitle->setAlignment(Qt::AlignCenter);
    welcomeTitle->setWordWrap(true);
    auto *welcomeText = new QLabel(
        QStringLiteral("Edit validated JSON and operation graphs, inspect policy-filtered databases,\n"
                       "and collaborate through ranked team spaces, notes and secure calls."),
        m_welcomePage);
    welcomeText->setObjectName(QStringLiteral("welcomeText"));
    welcomeText->setAlignment(Qt::AlignCenter);
    welcomeText->setWordWrap(true);
    auto *welcomeButtons = new QWidget(m_welcomePage);
    auto *welcomeButtonLayout = new QHBoxLayout(welcomeButtons);
    welcomeButtonLayout->setContentsMargins(0, 8, 0, 0);
    welcomeButtonLayout->setSpacing(10);
    auto *openWorkspaceButton = new QPushButton(QStringLiteral("Open workspace"), welcomeButtons);
    openWorkspaceButton->setObjectName(QStringLiteral("primaryButton"));
    auto *connectServerButton = new QPushButton(QStringLiteral("Connect to server"), welcomeButtons);
    welcomeButtonLayout->addWidget(openWorkspaceButton);
    welcomeButtonLayout->addWidget(connectServerButton);
    connect(openWorkspaceButton, &QPushButton::clicked, this, &MainWindow::openLocalWorkspace);
    connect(connectServerButton, &QPushButton::clicked, this, &MainWindow::connectToServer);
    welcomeLayout->addWidget(welcomeLogo, 0, Qt::AlignCenter);
    welcomeLayout->addWidget(welcomeBadge);
    welcomeLayout->addWidget(welcomeTitle);
    welcomeLayout->addWidget(welcomeText);
    welcomeLayout->addWidget(welcomeButtons, 0, Qt::AlignCenter);
    m_codeEditor = new CodeEditor(m_workspace);
    auto font = QFontDatabase::systemFont(QFontDatabase::FixedFont);
    font.setPointSize(11);
    m_codeEditor->setFont(font);
    new JsonHighlighter(m_codeEditor->document());
    m_codeEditor->setPlaceholderText(QStringLiteral("Open app.json or a configuration fragment to begin."));
    m_visualDesigner = new VisualDesigner(m_workspace);
    m_graphEditor = new NodeGraphEditor(m_workspace);
    m_workspace->addWidget(m_welcomePage);
    m_workspace->addWidget(m_codeEditor);
    m_workspace->addWidget(m_visualDesigner);
    m_workspace->addWidget(m_graphEditor);
    contentLayout->addWidget(m_workspace, 1);
    root->addWidget(m_sidebar);
    root->addWidget(content);
    root->setStretchFactor(0, 0);
    root->setStretchFactor(1, 1);
    root->setSizes({272, 1208});
    connect(root, &QSplitter::splitterMoved, this, [this](int, int) {
        if (m_sidebarExpanded && m_sidebarAnimation == nullptr) {
            m_sidebarWidth = qBound(220, m_sidebar->width(), 320);
        }
    });

    m_pluginToolBar = addToolBar(QStringLiteral("Plugin tools"));
    m_pluginToolBar->setObjectName(QStringLiteral("pluginToolBar"));
    m_pluginToolBar->setMovable(false);
    m_pluginToolBar->hide();
    m_pythonDock = new QDockWidget(QStringLiteral("Python SDK Integration"), this);
    m_pythonDock->setObjectName(QStringLiteral("pythonSdkDock"));
    m_pythonDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea);
    m_pythonDock->setMinimumWidth(300);
    m_pythonPanel = new PythonSdkPanel(m_pythonDock);
    m_pythonDock->setWidget(m_pythonPanel);
    addDockWidget(Qt::RightDockWidgetArea, m_pythonDock);
    m_pythonDock->hide();
    m_teamDock = new QDockWidget(QStringLiteral("Server Team Workspace"), this);
    m_teamDock->setObjectName(QStringLiteral("teamWorkspaceDock"));
    m_teamDock->setAllowedAreas(Qt::LeftDockWidgetArea | Qt::RightDockWidgetArea | Qt::BottomDockWidgetArea);
    m_teamDock->setMinimumWidth(340);
    m_teamWorkspace = new TeamWorkspace(m_api, m_teamDock);
    m_teamDock->setWidget(m_teamWorkspace);
    addDockWidget(Qt::RightDockWidgetArea, m_teamDock);
    m_teamDock->hide();
    statusBar()->setObjectName(QStringLiteral("statusBar"));
    statusBar()->showMessage(QStringLiteral("Ready"));

    connect(m_projects, &QListWidget::itemSelectionChanged, this, &MainWindow::selectProject);
    connect(m_documents, &QTreeWidget::itemDoubleClicked, this, &MainWindow::openSelectedDocument);
}

void MainWindow::buildActions()
{
    auto *fileMenu = menuBar()->addMenu(QStringLiteral("&File"));
    auto *connectAction = fileMenu->addAction(QStringLiteral("Connect to server…"), this, &MainWindow::connectToServer);
    connectAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Shift+C")));
    auto *openAction = fileMenu->addAction(QStringLiteral("Open local workspace…"), this, &MainWindow::openLocalWorkspace);
    openAction->setShortcut(QKeySequence::Open);
    fileMenu->addSeparator();
    m_saveAction = fileMenu->addAction(QStringLiteral("Save"), this, &MainWindow::saveDocument);
    m_saveAction->setShortcut(QKeySequence::Save);
    m_saveAction->setEnabled(false);
    m_validateAction = fileMenu->addAction(QStringLiteral("Validate project"), this, &MainWindow::validateProject);
    m_validateAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Shift+V")));
    m_validateAction->setEnabled(false);
    m_createAction = fileMenu->addAction(QStringLiteral("Create project…"), this, &MainWindow::createProject);
    m_createAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Shift+N")));
    auto *templateAction = fileMenu->addAction(QStringLiteral("New from template…"), this, &MainWindow::createFromTemplate);
    templateAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Alt+N")));
    m_graphCreateAction = fileMenu->addAction(QStringLiteral("New operation graph…"), this, &MainWindow::createGraph);
    m_graphCreateAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+Shift+G")));
    fileMenu->addSeparator();
    m_disconnectAction = fileMenu->addAction(QStringLiteral("Disconnect"), this, &MainWindow::disconnectServer);
    m_disconnectAction->setEnabled(false);
    auto *quitAction = fileMenu->addAction(QStringLiteral("Quit"), qApp, &QApplication::quit);
    quitAction->setShortcut(QKeySequence::Quit);

    auto *viewMenu = menuBar()->addMenu(QStringLiteral("&View"));
    auto *codeAction = viewMenu->addAction(QStringLiteral("Code mode"), this, &MainWindow::showCodeMode);
    codeAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+1")));
    auto *visualAction = viewMenu->addAction(QStringLiteral("Visual mode"), this, &MainWindow::showVisualMode);
    visualAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+2")));
    auto *graphModeAction = viewMenu->addAction(QStringLiteral("Graph mode"), this, &MainWindow::showGraphMode);
    graphModeAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+3")));
    auto *sidebarAction = viewMenu->addAction(QStringLiteral("Toggle sidebar"), this, &MainWindow::toggleSidebar);
    sidebarAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+\\")));

    auto *settingsMenu = menuBar()->addMenu(QStringLiteral("&Settings"));
    auto *settingsAction = settingsMenu->addAction(QStringLiteral("Network and editor settings…"),
                                                    this, &MainWindow::showSettings);
    settingsAction->setShortcut(QKeySequence::Preferences);

    auto *pluginMenu = menuBar()->addMenu(QStringLiteral("&Plugins"));
    pluginMenu->addAction(QStringLiteral("Import plugin from ZIP…"), this,
                          &MainWindow::importPluginZip);
    pluginMenu->addSeparator();
    pluginMenu->addAction(QStringLiteral("Manage plugins…"), this, &MainWindow::managePlugins);
    pluginMenu->addAction(QStringLiteral("Browse Forge plugin catalog…"), this, &MainWindow::browsePluginCatalog);
    pluginMenu->addAction(QStringLiteral("Reload approved plugins"), this, &MainWindow::reloadPlugins);

    auto *integrationMenu = menuBar()->addMenu(QStringLiteral("&Integrations"));
    auto *teamAction = integrationMenu->addAction(QStringLiteral("Server team workspace"));
    teamAction->setCheckable(true);
    teamAction->setShortcut(QKeySequence(QStringLiteral("Ctrl+4")));
    connect(teamAction, &QAction::toggled, m_teamDock, &QDockWidget::setVisible);
    connect(m_teamDock, &QDockWidget::visibilityChanged, teamAction, &QAction::setChecked);
    connect(m_teamDock, &QDockWidget::visibilityChanged, this, [this](bool visible) {
        if (visible) {
            QTimer::singleShot(0, this, &MainWindow::arrangeTeamDock);
        } else if (centralWidget() != nullptr) {
            centralWidget()->show();
        }
    });
    auto *pythonAction = integrationMenu->addAction(QStringLiteral("Python SDK panel"));
    pythonAction->setCheckable(true);
    connect(pythonAction, &QAction::toggled, m_pythonDock, &QDockWidget::setVisible);
    connect(m_pythonDock, &QDockWidget::visibilityChanged, pythonAction, &QAction::setChecked);

    auto *helpMenu = menuBar()->addMenu(QStringLiteral("&Help"));
    helpMenu->addAction(QStringLiteral("About JSON API Forge Editor"), this, &MainWindow::showAbout);
}

void MainWindow::applyStyle()
{
    QFile style(QStringLiteral(":/styles/dark.qss"));
    if (style.open(QIODevice::ReadOnly)) {
        qApp->setStyleSheet(QString::fromUtf8(style.readAll()));
    }
}

void MainWindow::applyPreferences()
{
    m_preferences.clamp();
    if (m_api != nullptr) {
        m_api->applyPreferences(m_preferences);
    }
    if (m_graphEditor != nullptr) {
        m_graphEditor->setInteractionSettings(m_preferences.rightDragPan,
                                              m_preferences.graphZoomSensitivity);
    }
}

void MainWindow::showSettings()
{
    EditorSettingsDialog dialog(m_preferences, this);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    m_preferences = dialog.preferences();
    m_preferences.save();
    QSettings settings;
    const auto lastServer = settings.value(QStringLiteral("connection/lastServer")).toString();
    if (!lastServer.isEmpty() && !m_preferences.serverHistory.contains(lastServer)) {
        settings.remove(QStringLiteral("connection/lastServer"));
    }
    applyPreferences();
    showStatusMessage(QStringLiteral("Network and editor settings applied immediately."), 5000);
}

void MainWindow::restoreWindowLayout()
{
    QSettings settings;
    const auto geometry = settings.value(QStringLiteral("window/geometry")).toByteArray();
    const auto state = settings.value(QStringLiteral("window/state-v1")).toByteArray();
    if (!geometry.isEmpty()) {
        restoreGeometry(geometry);
    }
    if (!state.isEmpty()) {
        restoreState(state, 1);
    }
    m_sidebarExpanded = settings.value(QStringLiteral("window/sidebarExpanded"), true).toBool();

    QScreen *screen = QGuiApplication::screenAt(frameGeometry().center());
    if (screen == nullptr) {
        screen = QGuiApplication::primaryScreen();
    }
    if (screen != nullptr) {
        const QRect available = screen->availableGeometry();
        resize(qMin(width(), available.width()), qMin(height(), available.height()));
        const QRect frame = frameGeometry();
        const int maximumX = qMax(available.left(), available.right() - frame.width() + 1);
        const int maximumY = qMax(available.top(), available.bottom() - frame.height() + 1);
        move(qBound(available.left(), frame.left(), maximumX), qBound(available.top(), frame.top(), maximumY));
    }

    if (width() < 900 && m_sidebarExpanded) {
        m_sidebarExpanded = false;
        m_sidebarAutoCollapsed = true;
    }
    applySidebarState();
    if (width() < 1100 && m_pythonDock->isVisible() && m_teamDock->isVisible()) {
        tabifyDockWidget(m_pythonDock, m_teamDock);
        m_teamDock->raise();
    }
}

void MainWindow::connectToServer()
{
    QSettings settings;
    ConnectionDialog dialog(QUrl(settings.value(QStringLiteral("connection/lastServer")).toString()), this);
    if (m_reopenSignIn) {
        dialog.setInitialMode(ConnectionDialog::AuthenticationMode::SignIn,
                              m_pendingSetupUsername);
    } else {
        dialog.setInitialMode(ConnectionDialog::AuthenticationMode::SignIn,
                              settings.value(QStringLiteral("connection/lastUsername")).toString());
    }
    m_reopenSignIn = false;
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    QUrl normalized;
    QString error;
    if (!ApiClient::normalizeServerUrl(dialog.serverUrl(), dialog.allowInsecureHttp(),
                                       &normalized, &error)) {
        QMessageBox::warning(this, QStringLiteral("Cannot connect"), error);
        return;
    }
    const bool sameServer = m_remoteMode && m_api->hasServer()
        && normalized == m_api->serverUrl();
    if (m_dirty && !sameServer && !confirmDiscard()) {
        return;
    }
    const QScopedValueRollback<bool> disconnecting(m_disconnecting, true);
    m_teamWorkspace->reset();
    if (!m_api->configureServer(normalized, dialog.allowInsecureHttp(), &error)) {
        QMessageBox::warning(this, QStringLiteral("Cannot connect"), error);
        return;
    }
    settings.setValue(QStringLiteral("connection/lastServer"), normalized.toString());
    settings.setValue(QStringLiteral("connection/lastUsername"), dialog.username());
    m_preferences.serverHistory.removeAll(normalized.toString());
    m_preferences.serverHistory.prepend(normalized.toString());
    while (m_preferences.serverHistory.size() > 10) {
        m_preferences.serverHistory.removeLast();
    }
    m_preferences.save();
    m_remoteMode = true;
    updateConnectionActions();
    if (!sameServer) {
        m_workspaceRoot.clear();
        m_currentProject.clear();
        m_currentProjectPath.clear();
        m_currentDocument.clear();
        m_currentSha256.clear();
        m_projects->clear();
        m_documents->clear();
        setDirty(false);
        showWelcome();
    }
    m_connectionLabel->setText(QStringLiteral("●  Connecting…"));
    m_connectionLabel->setObjectName(QStringLiteral("connectionPending"));
    m_connectionLabel->style()->unpolish(m_connectionLabel);
    m_connectionLabel->style()->polish(m_connectionLabel);
    switch (dialog.authenticationMode()) {
    case ConnectionDialog::AuthenticationMode::SignIn:
        m_pendingSetupUsername.clear();
        m_api->login(dialog.username(), dialog.password());
        break;
    case ConnectionDialog::AuthenticationMode::JoinInvitation:
        m_pendingSetupUsername.clear();
        m_api->registerMember(dialog.invitation(), dialog.username(), dialog.password(), dialog.displayName());
        break;
    case ConnectionDialog::AuthenticationMode::FounderSetup:
        m_pendingSetupUsername = dialog.username();
        m_api->setupFounder(dialog.setupToken(), dialog.username(), dialog.password(), dialog.displayName());
        break;
    }
}

void MainWindow::disconnectServer()
{
    if (!confirmDiscard()) {
        return;
    }
    const QScopedValueRollback<bool> disconnecting(m_disconnecting, true);
    if (m_api->isConfigured()) {
        m_api->logout();
    }
    m_remoteMode = false;
    m_api->clearCredentials();
    updateConnectionActions();
    m_projects->clear();
    m_documents->clear();
    m_currentProject.clear();
    m_currentProjectPath.clear();
    m_currentDocument.clear();
    m_currentSha256.clear();
    m_pendingSaveProject.clear();
    m_pendingSaveDocument.clear();
    m_pendingSaveDigest.clear();
    m_codeEditor->clear();
    m_connectionLabel->setText(QStringLiteral("●  Not connected"));
    m_connectionLabel->setObjectName(QStringLiteral("connectionOffline"));
    m_policyReadOnly = true;
    m_policyCreate = false;
    m_policyHooks = false;
    m_policyGraphs = false;
    m_policyMaxBytes = 0;
    m_teamWorkspace->reset();
    m_teamDock->hide();
    updatePolicyPanel();
    setDirty(false);
    showWelcome();
}

void MainWindow::openLocalWorkspace()
{
    if (!confirmDiscard()) {
        return;
    }
    const auto selected = QFileDialog::getExistingDirectory(this, QStringLiteral("Open Forge workspace"), m_workspaceRoot);
    if (selected.isEmpty()) {
        return;
    }
    const QScopedValueRollback<bool> disconnecting(m_disconnecting, true);
    m_remoteMode = false;
    m_api->clearCredentials();
    updateConnectionActions();
    m_teamWorkspace->reset();
    m_teamDock->hide();
    populateLocalProjects(selected);
}

void MainWindow::populateLocalProjects(const QString &rootPath)
{
    m_projects->clear();
    m_documents->clear();
    QDir selected(rootPath);
    QDir apps = selected;
    if (QFileInfo(selected.filePath(QStringLiteral("app.json"))).isFile()) {
        apps = QDir(QFileInfo(selected.absolutePath()).absolutePath());
    } else if (QDir(selected.filePath(QStringLiteral("app"))).exists()) {
        apps = QDir(selected.filePath(QStringLiteral("app")));
    }
    m_workspaceRoot = apps.absolutePath();
    const auto projects = apps.entryInfoList(QDir::Dirs | QDir::NoDotAndDotDot | QDir::Readable, QDir::Name);
    for (const auto &project : projects) {
        if (!QFileInfo(QDir(project.absoluteFilePath()).filePath(QStringLiteral("app.json"))).isFile() || project.isSymLink()) {
            continue;
        }
        auto *item = new QListWidgetItem(project.fileName(), m_projects);
        item->setData(ProjectPathRole, project.absoluteFilePath());
        item->setData(ProjectSourceRole, LocalSource);
    }
    if (QFileInfo(apps.filePath(QStringLiteral("app.json"))).isFile()) {
        auto *item = new QListWidgetItem(QFileInfo(apps.absolutePath()).fileName(), m_projects);
        item->setData(ProjectPathRole, apps.absolutePath());
        item->setData(ProjectSourceRole, LocalSource);
    }
    m_connectionLabel->setText(QStringLiteral("●  Local workspace"));
    m_connectionLabel->setObjectName(QStringLiteral("connectionLocal"));
    m_policyReadOnly = false;
    m_policyCreate = true;
    m_policyHooks = true;
    m_policyGraphs = true;
    m_policyMaxBytes = 0;
    updatePolicyPanel();
    m_createAction->setEnabled(true);
    if (m_graphCreateAction != nullptr) {
        m_graphCreateAction->setEnabled(true);
    }
    if (m_projects->count() > 0) {
        m_projects->setCurrentRow(0);
    } else {
        showStatusMessage(QStringLiteral("No app.json projects found. Use File → Create project."), 6000);
    }
}

void MainWindow::selectProject()
{
    if (!confirmDiscard()) {
        QSignalBlocker blocker(m_projects);
        for (int row = 0; row < m_projects->count(); ++row) {
            auto *previous = m_projects->item(row);
            if (previous->text() == m_currentProject) {
                m_projects->setCurrentItem(previous);
                break;
            }
        }
        return;
    }
    auto *item = m_projects->currentItem();
    if (item == nullptr) {
        return;
    }
    if (item->data(ProjectPathRole).toString() == m_currentProjectPath
        && item->text() == m_currentProject) {
        return;
    }
    m_currentProject = item->text();
    m_currentProjectPath = item->data(ProjectPathRole).toString();
    m_documents->clear();
    m_currentDocument.clear();
    showWelcome();
    m_saveAction->setEnabled(false);
    m_validateAction->setEnabled(true);
    if (item->data(ProjectSourceRole).toInt() == RemoteSource) {
        m_teamWorkspace->setCapabilities(QJsonObject{});
        m_teamWorkspace->setProject(m_currentProject);
        m_api->fetchCapabilities(m_currentProject);
        m_api->fetchDocuments(m_currentProject);
    } else {
        m_teamWorkspace->setProject(QString());
        populateLocalDocuments(m_currentProjectPath);
    }
}

void MainWindow::populateLocalDocuments(const QString &projectPath)
{
    m_documents->clear();
    auto add = [](QTreeWidgetItem *parent, const QString &relative) {
        auto *item = new QTreeWidgetItem(parent, {QFileInfo(relative).fileName()});
        item->setData(0, DocumentPathRole, relative);
        return item;
    };
    auto *root = new QTreeWidgetItem(m_documents, {QStringLiteral("Project")});
    add(root, QStringLiteral("app.json"));
    auto *config = new QTreeWidgetItem(m_documents, {QStringLiteral("Config fragments")});
    const QDir configDir(QDir(projectPath).filePath(QStringLiteral("config")));
    for (const auto &file : configDir.entryList({QStringLiteral("*.json")}, QDir::Files | QDir::Readable, QDir::Name)) {
        add(config, QStringLiteral("config/%1").arg(file));
    }
    auto *hooks = new QTreeWidgetItem(m_documents, {QStringLiteral("Python hooks")});
    const QDir hooksDir(QDir(projectPath).filePath(QStringLiteral("hooks")));
    for (const auto &file : hooksDir.entryList({QStringLiteral("*.py")}, QDir::Files | QDir::Readable, QDir::Name)) {
        add(hooks, QStringLiteral("hooks/%1").arg(file));
    }
    auto *graphs = new QTreeWidgetItem(m_documents, {QStringLiteral("Operation graphs")});
    const QDir graphsDir(QDir(projectPath).filePath(QStringLiteral("graphs")));
    for (const auto &file : graphsDir.entryList({QStringLiteral("*.forgegraph.json")}, QDir::Files | QDir::Readable, QDir::Name)) {
        add(graphs, QStringLiteral("graphs/%1").arg(file));
    }
    root->setExpanded(true);
    config->setExpanded(true);
    hooks->setExpanded(true);
    graphs->setExpanded(true);
}

void MainWindow::openSelectedDocument()
{
    if (!confirmDiscard()) {
        return;
    }
    auto *item = m_documents->currentItem();
    if (item == nullptr) {
        return;
    }
    const auto relative = item->data(0, DocumentPathRole).toString();
    if (relative.isEmpty()) {
        return;
    }
    if (m_remoteMode) {
        m_pendingOpenOperation = QStringLiteral("document:%1:%2").arg(m_currentProject, relative);
        m_pendingOpenGeneration = m_editGeneration;
        m_api->fetchDocument(m_currentProject, relative);
        return;
    }
    QFile file(QDir(m_currentProjectPath).filePath(relative));
    if (!file.open(QIODevice::ReadOnly)) {
        QMessageBox::warning(this, QStringLiteral("Cannot open document"), file.errorString());
        return;
    }
    const auto bytes = file.readAll();
    loadDocument(relative, bytes, DocumentCodec::sha256(bytes));
}

void MainWindow::loadDocument(const QString &path, const QByteArray &content, const QString &sha256)
{
    m_workspace->setCurrentWidget(m_codeEditor);
    m_pendingOpenOperation.clear();
    m_currentDocument = path;
    m_currentSha256 = sha256;
    m_updatingEditor = true;
    m_codeEditor->setPlainText(QString::fromUtf8(content));
    m_codeEditor->document()->setModified(false);
    m_updatingEditor = false;
    m_breadcrumb->setText(QStringLiteral("%1  /  %2  ·  %3").arg(currentModeName(m_remoteMode), m_currentProject, path));
    m_breadcrumb->setToolTip(m_breadcrumb->text());
    const bool jsonDocument = path.endsWith(QStringLiteral(".json"), Qt::CaseSensitive);
    const bool graphDocument = path.startsWith(QStringLiteral("graphs/"))
        && path.endsWith(QStringLiteral(".forgegraph.json"), Qt::CaseSensitive);
    m_codeButton->setEnabled(true);
    m_visualButton->setEnabled(jsonDocument && !graphDocument);
    m_graphButton->setEnabled(graphDocument);
    m_saveAction->setEnabled(!m_remoteMode || !m_policyReadOnly);
    setDirty(false);
    if (graphDocument) {
        QString graphError;
        QJsonObject graph;
        if (DocumentCodec::parseObject(content, &graph, &graphError) && m_graphEditor->setDocument(graph, &graphError)) {
            showGraphMode();
        } else {
            showCodeMode();
            showStatusMessage(QStringLiteral("Graph opened as code because it is invalid: %1").arg(graphError), 8000);
        }
    } else {
        showCodeMode();
    }
}

bool MainWindow::prepareCurrentJson(QJsonObject *object, QByteArray *bytes)
{
    if (m_currentDocument.isEmpty()) {
        return false;
    }
    const auto current = currentEditorBytes();
    if (m_currentDocument.endsWith(QStringLiteral(".json"))) {
        QString error;
        QJsonObject parsed;
        if (!DocumentCodec::parseObject(current, &parsed, &error)) {
            QMessageBox::warning(this, QStringLiteral("Invalid JSON"), error);
            return false;
        }
        if (object != nullptr) {
            *object = parsed;
        }
    }
    if (bytes != nullptr) {
        *bytes = current;
    }
    return true;
}

QByteArray MainWindow::currentEditorBytes() const
{
    if (m_workspace->currentWidget() == m_graphEditor
        && m_currentDocument.endsWith(QStringLiteral(".forgegraph.json"))) {
        return DocumentCodec::prettyJson(m_graphEditor->document());
    }
    if (m_workspace->currentWidget() == m_visualDesigner
        && m_currentDocument.endsWith(QStringLiteral(".json"))) {
        return DocumentCodec::prettyJson(m_visualDesigner->document());
    }
    return m_codeEditor->toPlainText().toUtf8();
}

void MainWindow::saveDocument()
{
    QByteArray bytes;
    if (!prepareCurrentJson(nullptr, &bytes)) {
        return;
    }
    if (m_remoteMode) {
        if (m_policyReadOnly) {
            QMessageBox::information(this, QStringLiteral("Read-only policy"), QStringLiteral("This server does not permit editor writes."));
            return;
        }
        if (m_currentDocument.startsWith(QStringLiteral("hooks/")) && !m_policyHooks) {
            QMessageBox::warning(this, QStringLiteral("Hook policy"), QStringLiteral("Remote Python hook editing is disabled by the server."));
            return;
        }
        if (m_currentDocument.startsWith(QStringLiteral("graphs/")) && !m_policyGraphs) {
            QMessageBox::warning(this, QStringLiteral("Graph policy"), QStringLiteral("Remote operation graph editing is disabled by the server."));
            return;
        }
        if (m_policyMaxBytes > 0 && bytes.size() > m_policyMaxBytes) {
            QMessageBox::warning(this, QStringLiteral("Document too large"),
                                 QStringLiteral("This document is %1 bytes; the server policy allows at most %2 bytes.")
                                     .arg(bytes.size()).arg(m_policyMaxBytes));
            return;
        }
        m_pendingSaveProject = m_currentProject;
        m_pendingSaveDocument = m_currentDocument;
        m_pendingSaveDigest = DocumentCodec::sha256(bytes);
        m_saveAction->setEnabled(false);
        m_api->saveDocument(m_currentProject, m_currentDocument, bytes, m_currentSha256);
        return;
    }
    if (!DocumentCodec::isSafeDocumentPath(m_currentDocument, true)) {
        QMessageBox::critical(this, QStringLiteral("Unsafe path"), QStringLiteral("The selected file is outside the editor document policy."));
        return;
    }
    const auto target = QDir(m_currentProjectPath).filePath(m_currentDocument);
    QString error;
    if (!DocumentCodec::saveAtomically(target, bytes, &error)) {
        QMessageBox::critical(this, QStringLiteral("Save failed"), error);
        return;
    }
    m_currentSha256 = DocumentCodec::sha256(bytes);
    setDirty(false);
    if (m_currentDocument.startsWith(QStringLiteral("graphs/"))) {
        populateLocalDocuments(m_currentProjectPath);
    }
    showStatusMessage(QStringLiteral("Saved and fsync-committed %1").arg(m_currentDocument));
}

void MainWindow::validateProject()
{
    if (m_currentProject.isEmpty()) {
        return;
    }
    if (m_remoteMode) {
        m_api->validateProject(m_currentProject);
        return;
    }
    bool valid = true;
    QStringList errors;
    const QDir project(m_currentProjectPath);
    QStringList paths{QStringLiteral("app.json")};
    const QDir config(project.filePath(QStringLiteral("config")));
    for (const auto &name : config.entryList({QStringLiteral("*.json")}, QDir::Files, QDir::Name)) {
        paths.append(QStringLiteral("config/%1").arg(name));
    }
    for (const auto &path : paths) {
        QFile file(project.filePath(path));
        QJsonObject object;
        QString error;
        if (!file.open(QIODevice::ReadOnly) || !DocumentCodec::parseObject(file.readAll(), &object, &error)) {
            valid = false;
            errors.append(QStringLiteral("%1: %2").arg(path, file.isOpen() ? error : file.errorString()));
        }
    }
    const QDir graphs(project.filePath(QStringLiteral("graphs")));
    for (const auto &name : graphs.entryList({QStringLiteral("*.forgegraph.json")}, QDir::Files, QDir::Name)) {
        QFile file(graphs.filePath(name));
        QJsonObject object;
        QString error;
        GraphModel graph;
        if (!file.open(QIODevice::ReadOnly) || !DocumentCodec::parseObject(file.readAll(), &object, &error)
            || !graph.setDocument(object, &error)) {
            valid = false;
            errors.append(QStringLiteral("graphs/%1: %2").arg(name, file.isOpen() ? error : file.errorString()));
        }
    }
    if (valid) {
        QMessageBox::information(this, QStringLiteral("Local JSON check"),
                                 QStringLiteral("All project JSON files are syntactically valid objects. Run `forge validate` for full merged schema validation."));
    } else {
        QMessageBox::warning(this, QStringLiteral("Validation failed"), errors.join(u'\n'));
    }
}

void MainWindow::createProject()
{
    bool ok = false;
    const auto name = QInputDialog::getText(this, QStringLiteral("Create project"), QStringLiteral("Directory/name"), QLineEdit::Normal,
                                            QString(), &ok)
                          .trimmed();
    if (!ok || name.isEmpty()) {
        return;
    }
    const auto suggestedSlug = QString(name).toLower().replace(QRegularExpression(QStringLiteral("[^a-z0-9]+")), QStringLiteral("-"))
                                   .remove(QRegularExpression(QStringLiteral("^-|-$")));
    const auto slug = QInputDialog::getText(this, QStringLiteral("Create project"), QStringLiteral("Slug"), QLineEdit::Normal,
                                            suggestedSlug, &ok)
                          .trimmed();
    if (!ok || slug.isEmpty()) {
        return;
    }
    if (m_remoteMode) {
        if (!m_policyCreate || m_policyReadOnly) {
            QMessageBox::warning(this, QStringLiteral("Server policy"), QStringLiteral("Remote project creation is disabled."));
            return;
        }
        m_api->createProject(name, slug);
        return;
    }
    if (m_workspaceRoot.isEmpty() || name.contains(u'/') || name.contains(u'\\') || name == QStringLiteral(".")
        || name == QStringLiteral("..")) {
        QMessageBox::warning(this, QStringLiteral("Invalid project"), QStringLiteral("Choose a local workspace and a safe directory name."));
        return;
    }
    const QDir apps(m_workspaceRoot);
    const auto target = apps.filePath(name);
    if (QFileInfo::exists(target) || !QDir().mkpath(QDir(target).filePath(QStringLiteral("config")))) {
        QMessageBox::warning(this, QStringLiteral("Create failed"), QStringLiteral("The target exists or could not be created."));
        return;
    }
    QDir().mkpath(QDir(target).filePath(QStringLiteral("hooks")));
    const QJsonObject manifest{{QStringLiteral("$schema"), QStringLiteral("../../schemas/manifest.schema.json")},
                               {QStringLiteral("slug"), slug},
                               {QStringLiteral("name"), name},
                               {QStringLiteral("version"), QStringLiteral("1.0.0")},
                               {QStringLiteral("api_prefix"), QStringLiteral("/api/%1/v1").arg(slug)}};
    const QJsonObject databases{
        {QStringLiteral("$schema"), QStringLiteral("../../../schemas/fragment.schema.json")},
        {QStringLiteral("databases"),
         QJsonObject{{QStringLiteral("primary"),
                      QJsonObject{{QStringLiteral("url"),
                                   QStringLiteral("$env:%1_DATABASE_URL:-sqlite+aiosqlite:///./data/%2.db")
                                       .arg(QString(slug).toUpper().replace(u'-', u'_'), slug)}}}}}};
    QString error;
    if (!DocumentCodec::saveAtomically(QDir(target).filePath(QStringLiteral("app.json")), DocumentCodec::prettyJson(manifest), &error)
        || !DocumentCodec::saveAtomically(QDir(target).filePath(QStringLiteral("config/10-databases.json")),
                                          DocumentCodec::prettyJson(databases), &error)) {
        QMessageBox::critical(this, QStringLiteral("Create failed"), error);
        return;
    }
    populateLocalProjects(m_workspaceRoot);
    showStatusMessage(QStringLiteral("Created %1. Full schema validation remains available through the server/CLI.").arg(name), 6000);
}

void MainWindow::createGraph()
{
    if (m_currentProject.isEmpty()) {
        QMessageBox::information(this, QStringLiteral("Choose a project"),
                                 QStringLiteral("Select a local or remote project before creating an operation graph."));
        return;
    }
    if (!confirmDiscard()) {
        return;
    }
    if (m_remoteMode && (m_policyReadOnly || !m_policyGraphs)) {
        QMessageBox::warning(this, QStringLiteral("Server policy"),
                             QStringLiteral("This server does not permit operation graph creation."));
        return;
    }
    bool ok = false;
    const auto graphName = QInputDialog::getText(this, QStringLiteral("New operation graph"),
                                                  QStringLiteral("Graph file name (without extension)"), QLineEdit::Normal,
                                                  QStringLiteral("operation-flow"), &ok)
                               .trimmed();
    static const QRegularExpression GraphNamePattern(QStringLiteral(R"(^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$)"));
    if (!ok) {
        return;
    }
    if (!GraphNamePattern.match(graphName).hasMatch()) {
        QMessageBox::warning(this, QStringLiteral("Invalid graph name"),
                             QStringLiteral("Use 1–64 lowercase letters, digits, dots, underscores or hyphens."));
        return;
    }
    const auto target = QInputDialog::getText(this, QStringLiteral("New operation graph"),
                                               QStringLiteral("Compiled config target"), QLineEdit::Normal,
                                               QStringLiteral("config/50-%1.json").arg(graphName), &ok)
                            .trimmed();
    static const QRegularExpression TargetPattern(QStringLiteral(R"(^config/[A-Za-z0-9][A-Za-z0-9._-]{0,95}\.json$)"));
    if (!ok) {
        return;
    }
    if (!TargetPattern.match(target).hasMatch()) {
        QMessageBox::warning(this, QStringLiteral("Invalid target"),
                             QStringLiteral("The compiled target must be a direct config/*.json path."));
        return;
    }
    const auto relative = QStringLiteral("graphs/%1.forgegraph.json").arg(graphName);
    if (!m_remoteMode) {
        QDir project(m_currentProjectPath);
        if (!project.mkpath(QStringLiteral("graphs"))) {
            QMessageBox::warning(this, QStringLiteral("Create failed"), QStringLiteral("Could not create the graphs directory."));
            return;
        }
        if (QFileInfo::exists(project.filePath(relative))) {
            QMessageBox::warning(this, QStringLiteral("Create failed"), QStringLiteral("That graph already exists."));
            return;
        }
    }
    const auto bytes = DocumentCodec::prettyJson(NodeGraphEditor::starterDocument(target));
    loadDocument(relative, bytes, QStringLiteral("new"));
    setDirty(true);
    showStatusMessage(QStringLiteral("Created an unsaved starter graph. Connect nodes, configure properties, then save."), 8000);
}

void MainWindow::createFromTemplate()
{
    if (m_remoteMode) {
        QMessageBox::information(this, QStringLiteral("Local templates"),
                                 QStringLiteral("Templates are staged locally so you can review every file before deployment. Open a local workspace first."));
        return;
    }
    QString root = m_workspaceRoot;
    if (root.isEmpty()) {
        root = QFileDialog::getExistingDirectory(this, QStringLiteral("Choose workspace for the new project"));
    }
    if (root.isEmpty()) {
        return;
    }
    QString catalogError;
    const auto definitions = TemplateManager::templates(&catalogError);
    if (definitions.isEmpty()) {
        QMessageBox::critical(this, QStringLiteral("Template catalog"), catalogError);
        return;
    }
    QStringList choices;
    for (const auto &definition : definitions) {
        choices.append(QStringLiteral("%1  ·  %2").arg(definition.category, definition.name));
    }
    bool ok = false;
    const auto choice = QInputDialog::getItem(this, QStringLiteral("New from template"), QStringLiteral("Project template"), choices,
                                               0, false, &ok);
    if (!ok) {
        return;
    }
    const auto index = choices.indexOf(choice);
    if (index < 0) {
        return;
    }
    const auto &definition = definitions.at(index);
    const auto confirmation = QMessageBox::question(
        this, definition.name,
        QStringLiteral("%1\n\nCreate this template with secured CRUD, analytics RPC, realtime channel and an editable node graph?")
            .arg(definition.description),
        QMessageBox::Yes | QMessageBox::Cancel, QMessageBox::Yes);
    if (confirmation != QMessageBox::Yes) {
        return;
    }
    const auto suggestedDirectory = QString(definition.name).remove(QRegularExpression(QStringLiteral("[^A-Za-z0-9]+")));
    const auto directoryName = QInputDialog::getText(this, QStringLiteral("Project directory"), QStringLiteral("Directory name"),
                                                      QLineEdit::Normal, suggestedDirectory, &ok)
                                   .trimmed();
    if (!ok) {
        return;
    }
    const auto suggestedSlug = QString(directoryName).toLower().replace(QRegularExpression(QStringLiteral("[^a-z0-9]+")),
                                                                         QStringLiteral("-"))
                                   .remove(QRegularExpression(QStringLiteral("^-|-$")));
    const auto slug = QInputDialog::getText(this, QStringLiteral("Project slug"), QStringLiteral("Slug"), QLineEdit::Normal,
                                             suggestedSlug, &ok)
                          .trimmed();
    if (!ok) {
        return;
    }
    QString error;
    if (!TemplateManager::createProject(definition, root, directoryName, slug, &error)) {
        QMessageBox::critical(this, QStringLiteral("Template creation failed"), error);
        return;
    }
    populateLocalProjects(root);
    for (int row = 0; row < m_projects->count(); ++row) {
        if (m_projects->item(row)->text() == directoryName) {
            m_projects->setCurrentRow(row);
            break;
        }
    }
    showStatusMessage(QStringLiteral("Created %1 from the %2 template. Review it, then run forge validate.")
                          .arg(directoryName, definition.name),
                      8000);
}

void MainWindow::showCodeMode()
{
    if (m_currentDocument.isEmpty()) {
        showWelcome();
        return;
    }
    if (m_workspace->currentWidget() == m_visualDesigner && m_visualButton->isEnabled()) {
        m_updatingEditor = true;
        m_codeEditor->setPlainText(QString::fromUtf8(DocumentCodec::prettyJson(m_visualDesigner->document())));
        m_updatingEditor = false;
    } else if (m_workspace->currentWidget() == m_graphEditor && m_graphButton->isEnabled()) {
        m_updatingEditor = true;
        m_codeEditor->setPlainText(QString::fromUtf8(DocumentCodec::prettyJson(m_graphEditor->document())));
        m_updatingEditor = false;
    }
    m_workspace->setCurrentWidget(m_codeEditor);
    m_codeButton->setChecked(true);
    m_visualButton->setChecked(false);
    m_graphButton->setChecked(false);
    animateWorkspace(m_codeEditor);
}

void MainWindow::showWelcome()
{
    m_currentDocument.clear();
    m_currentSha256.clear();
    m_pendingOpenOperation.clear();
    setDirty(false);
    m_saveAction->setEnabled(false);
    m_codeButton->setChecked(false);
    m_codeButton->setEnabled(false);
    m_visualButton->setChecked(false);
    m_visualButton->setEnabled(false);
    m_graphButton->setChecked(false);
    m_graphButton->setEnabled(false);
    m_breadcrumb->setText(m_currentProject.isEmpty()
                              ? QStringLiteral("Choose a workspace or connect to a Forge server")
                              : QStringLiteral("%1  /  %2  ·  Choose a document")
                                    .arg(currentModeName(m_remoteMode), m_currentProject));
    m_breadcrumb->setToolTip(m_breadcrumb->text());
    m_workspace->setCurrentWidget(m_welcomePage);
}

void MainWindow::showVisualMode()
{
    if (m_workspace->currentWidget() == m_visualDesigner) {
        m_visualButton->setChecked(true);
        return;
    }
    if (!m_visualButton->isEnabled()) {
        return;
    }
    QJsonObject object;
    QString error;
    if (!DocumentCodec::parseObject(m_codeEditor->toPlainText().toUtf8(), &object, &error)) {
        QMessageBox::warning(this, QStringLiteral("Cannot open visual mode"), error);
        return;
    }
    m_visualDesigner->setDocument(object);
    m_workspace->setCurrentWidget(m_visualDesigner);
    m_codeButton->setChecked(false);
    m_visualButton->setChecked(true);
    m_graphButton->setChecked(false);
    animateWorkspace(m_visualDesigner);
}

void MainWindow::showGraphMode()
{
    if (m_workspace->currentWidget() == m_graphEditor) {
        m_graphButton->setChecked(true);
        return;
    }
    if (!m_graphButton->isEnabled()) {
        return;
    }
    QJsonObject object;
    QString error;
    if (!DocumentCodec::parseObject(m_codeEditor->toPlainText().toUtf8(), &object, &error)
        || !m_graphEditor->setDocument(object, &error)) {
        QMessageBox::warning(this, QStringLiteral("Cannot open graph mode"), error);
        return;
    }
    m_workspace->setCurrentWidget(m_graphEditor);
    m_codeButton->setChecked(false);
    m_visualButton->setChecked(false);
    m_graphButton->setChecked(true);
    animateWorkspace(m_graphEditor);
}

void MainWindow::animateWorkspace(QWidget *widget)
{
    auto *effect = new QGraphicsOpacityEffect(widget);
    widget->setGraphicsEffect(effect);
    auto *animation = new QPropertyAnimation(effect, "opacity", widget);
    animation->setDuration(150);
    animation->setStartValue(0.35);
    animation->setEndValue(1.0);
    animation->setEasingCurve(QEasingCurve::OutCubic);
    connect(animation, &QPropertyAnimation::finished, widget, [widget] { widget->setGraphicsEffect(nullptr); });
    animation->start(QAbstractAnimation::DeleteWhenStopped);
}

void MainWindow::applySidebarState()
{
    if (m_sidebarAnimation != nullptr) {
        m_sidebarAnimation->stop();
        m_sidebarAnimation->deleteLater();
        m_sidebarAnimation = nullptr;
    }
    m_sidebar->setMinimumWidth(m_sidebarExpanded ? 220 : 0);
    m_sidebar->setMaximumWidth(m_sidebarExpanded ? 320 : 0);
    m_sidebar->setVisible(m_sidebarExpanded);
    m_rootSplitter->setHandleWidth(m_sidebarExpanded ? 1 : 0);
    m_rootSplitter->handle(1)->setVisible(m_sidebarExpanded);
    m_rootSplitter->setSizes({m_sidebarExpanded ? m_sidebarWidth : 0,
                              qMax(1, m_rootSplitter->width() - (m_sidebarExpanded ? m_sidebarWidth : 0))});
    m_sidebarButton->setChecked(m_sidebarExpanded);
}

void MainWindow::toggleSidebar()
{
    if (m_sidebarExpanded && m_sidebarAnimation == nullptr) {
        m_sidebarWidth = qBound(220, m_sidebar->width(), 320);
    }
    m_sidebarAutoCollapsed = false;
    m_sidebarExpanded = !m_sidebarExpanded;
    const int startWidth = m_sidebar->isVisible() ? m_sidebar->width() : 0;
    if (m_sidebarAnimation != nullptr) {
        m_sidebarAnimation->stop();
        m_sidebarAnimation->deleteLater();
    }
    m_sidebar->setMinimumWidth(0);
    m_sidebar->setMaximumWidth(qMax(1, startWidth));
    m_sidebar->show();
    m_rootSplitter->setHandleWidth(0);
    m_rootSplitter->handle(1)->hide();
    m_sidebarButton->setChecked(m_sidebarExpanded);
    auto *animation = new QVariantAnimation(this);
    m_sidebarAnimation = animation;
    animation->setDuration(220);
    animation->setStartValue(startWidth);
    animation->setEndValue(m_sidebarExpanded ? m_sidebarWidth : 0);
    animation->setEasingCurve(QEasingCurve::OutCubic);
    connect(animation, &QVariantAnimation::valueChanged, this, [this](const QVariant &value) {
        const int sidebarWidth = value.toInt();
        m_sidebar->setMaximumWidth(sidebarWidth);
        m_rootSplitter->setSizes({sidebarWidth, qMax(1, m_rootSplitter->width() - sidebarWidth)});
    });
    connect(animation, &QVariantAnimation::finished, this, &MainWindow::applySidebarState);
    animation->start();
}

void MainWindow::updateConnectionActions()
{
    const bool connected = m_remoteMode && m_api->hasServer();
    m_disconnectButton->setVisible(connected);
    m_disconnectAction->setEnabled(connected);
    m_connectionLabel->setFixedWidth(qBound(110, m_connectionLabel->fontMetrics().horizontalAdvance(m_connectionLabel->text()) + 24, width() < 1100 ? 140 : 210));
    m_connectionLabel->setToolTip(m_connectionLabel->text());
    m_connectionLabel->style()->unpolish(m_connectionLabel);
    m_connectionLabel->style()->polish(m_connectionLabel);
}

void MainWindow::handleApiJson(const QString &operation, const QJsonObject &payload)
{
    if (operation == QStringLiteral("setup-reconcile")) {
        if (payload.value(QStringLiteral("initialized")).toBool(false)) {
            m_api->clearSession();
            m_connectionLabel->setText(QStringLiteral("●  Founder created · sign in"));
            m_connectionLabel->setObjectName(QStringLiteral("connectionPending"));
            QMessageBox::information(
                this, QStringLiteral("Founder setup completed"),
                QStringLiteral("The server reports that founder initialization completed. For safety, the setup request is not repeated. Sign in with the new account."));
            m_reopenSignIn = true;
            QTimer::singleShot(0, this, &MainWindow::connectToServer);
        } else {
            showApiErrorDialog(
                operation, 0,
                QStringLiteral("The setup request timed out and the server still reports that no founder is initialized."),
                QStringLiteral("timeout"),
                QStringLiteral("No mutation was replayed. Verify server health and the one-time setup configuration before trying again."));
        }
        return;
    }
    if (operation.startsWith(QStringLiteral("auth-"))) {
        if (operation == QStringLiteral("auth-logout")) {
            return;
        }
        m_sessionExpired = false;
        const auto profile = payload.value(QStringLiteral("profile")).toObject();
        m_connectionLabel->setText(
            QStringLiteral("●  %1 · @%2")
                .arg(profile.value(QStringLiteral("display_name")).toString(),
                     profile.value(QStringLiteral("username")).toString()));
        m_connectionLabel->setObjectName(QStringLiteral("connectionOnline"));
        m_connectionLabel->style()->unpolish(m_connectionLabel);
        m_connectionLabel->style()->polish(m_connectionLabel);
        m_teamWorkspace->setProject(m_currentProject);
        m_api->fetchCapabilities(m_currentProject);
        // Project discovery must not depend on a server-wide capability grant.
        m_api->fetchProjects();
        updateConnectionActions();
        m_teamDock->show();
        arrangeTeamDock();
        showStatusMessage(QStringLiteral("Account session established. Server roles and scopes remain authoritative."),
                          7000);
        m_pendingSetupUsername.clear();
        return;
    }
    if (operation == QStringLiteral("capabilities") || operation.startsWith(QStringLiteral("capabilities:"))) {
        if (operation.startsWith(QStringLiteral("capabilities:"))
            && operation != QStringLiteral("capabilities:%1").arg(m_currentProject)) { return; }
        if (operation == QStringLiteral("capabilities") && !m_currentProject.isEmpty()) { return; }
        m_policyReadOnly = payload.value(QStringLiteral("read_only")).toBool(true);
        m_policyCreate = payload.value(QStringLiteral("allow_create_projects")).toBool(false);
        m_policyHooks = payload.value(QStringLiteral("allow_hooks")).toBool(false);
        m_policyGraphs = payload.value(QStringLiteral("allow_graphs")).toBool(false);
        m_policyMaxBytes = payload.value(QStringLiteral("max_document_bytes")).toInt();
        m_teamWorkspace->setCapabilities(payload);
        updatePolicyPanel();
        m_createAction->setEnabled(m_policyCreate && !m_policyReadOnly);
        m_teamWorkspace->refreshAll();
        return;
    }
    if (operation == QStringLiteral("projects")) {
        if (!m_remoteMode || !m_api->isConfigured()) { return; }
        QSignalBlocker blocker(m_projects);
        m_projects->clear();
        for (const auto &entry : payload.value(QStringLiteral("projects")).toArray()) {
            const auto object = entry.toObject();
            auto *item = new QListWidgetItem(object.value(QStringLiteral("name")).toString(), m_projects);
            item->setText(object.value(QStringLiteral("directory")).toString(item->text()));
            item->setToolTip(object.value(QStringLiteral("slug")).toString());
            item->setData(ProjectPathRole, object.value(QStringLiteral("directory")).toString());
            item->setData(ProjectSourceRole, RemoteSource);
        }
        int selectedRow = -1;
        for (int row = 0; row < m_projects->count(); ++row) {
            if (m_projects->item(row)->text() == m_currentProject) { selectedRow = row; break; }
        }
        if (selectedRow >= 0) {
            m_projects->setCurrentRow(selectedRow);
        } else if (m_projects->count() > 0 && !m_dirty) {
            m_projects->setCurrentRow(0);
        }
        blocker.unblock();
        if (selectedRow < 0 && !m_dirty && m_projects->currentItem() != nullptr) { selectProject(); }
        return;
    }
    if (operation.startsWith(QStringLiteral("documents:"))) {
        if (!m_remoteMode || operation != QStringLiteral("documents:%1").arg(m_currentProject)) {
            return;
        }
        m_documents->clear();
        auto *root = new QTreeWidgetItem(m_documents, {QStringLiteral("Server documents")});
        for (const auto &entry : payload.value(QStringLiteral("documents")).toArray()) {
            const auto object = entry.toObject();
            const auto path = object.value(QStringLiteral("path")).toString();
            auto *item = new QTreeWidgetItem(root, {path});
            item->setData(0, DocumentPathRole, path);
            item->setToolTip(0, QStringLiteral("SHA-256 %1").arg(object.value(QStringLiteral("sha256")).toString()));
        }
        root->setExpanded(true);
        return;
    }
    if (operation == QStringLiteral("reconcile-save")) {
        m_connectionLabel->setText(QStringLiteral("●  Connected"));
        m_connectionLabel->setObjectName(QStringLiteral("connectionOnline"));
        const auto path = payload.value(QStringLiteral("path")).toString();
        const auto serverBytes = payload.value(QStringLiteral("content")).toString().toUtf8();
        const auto serverDigest = DocumentCodec::sha256(serverBytes);
        if (path != m_pendingSaveDocument || serverDigest != m_pendingSaveDigest) {
            m_saveAction->setEnabled(m_api->isConfigured() && !m_policyReadOnly && !m_currentDocument.isEmpty());
            showSaveConflict(payload);
            return;
        }
        if (m_currentProject == m_pendingSaveProject && m_currentDocument == m_pendingSaveDocument) {
            m_currentSha256 = payload.value(QStringLiteral("sha256")).toString(serverDigest);
            if (DocumentCodec::sha256(currentEditorBytes()) == m_pendingSaveDigest) {
                setDirty(false);
                showStatusMessage(QStringLiteral("The timed-out save was confirmed on the server."), 7000);
            } else {
                setDirty(true);
                showStatusMessage(QStringLiteral("The timed-out save succeeded; newer local edits remain unsaved."), 8000);
            }
        }
        m_pendingSaveProject.clear();
        m_pendingSaveDocument.clear();
        m_pendingSaveDigest.clear();
        m_saveAction->setEnabled(m_api->isConfigured() && !m_policyReadOnly && !m_currentDocument.isEmpty());
        return;
    }
    if (operation == QStringLiteral("conflict-document")) {
        showSaveConflict(payload);
        return;
    }
    if (operation.startsWith(QStringLiteral("document:"))) {
        if (!m_remoteMode || operation != m_pendingOpenOperation || m_pendingOpenGeneration != m_editGeneration) {
            return;
        }
        loadDocument(payload.value(QStringLiteral("path")).toString(), payload.value(QStringLiteral("content")).toString().toUtf8(),
                     payload.value(QStringLiteral("sha256")).toString());
        return;
    }
    if (operation.startsWith(QStringLiteral("save:"))) {
        if (m_currentProject == m_pendingSaveProject && m_currentDocument == m_pendingSaveDocument) {
            m_currentSha256 = payload.value(QStringLiteral("sha256")).toString();
            if (DocumentCodec::sha256(currentEditorBytes()) == m_pendingSaveDigest) {
                setDirty(false);
                showStatusMessage(QStringLiteral("Server validated and atomically saved %1").arg(m_currentDocument));
            } else {
                setDirty(true);
                showStatusMessage(QStringLiteral("The server saved the submitted revision; newer local edits remain unsaved."), 8000);
            }
        }
        const auto savedProject = m_pendingSaveProject;
        m_pendingSaveProject.clear();
        m_pendingSaveDocument.clear();
        m_pendingSaveDigest.clear();
        m_saveAction->setEnabled(m_api->isConfigured() && !m_policyReadOnly && !m_currentDocument.isEmpty());
        if (!savedProject.isEmpty()) {
            m_api->fetchDocuments(savedProject);
        }
        return;
    }
    if (operation.startsWith(QStringLiteral("validate:"))) {
        QMessageBox::information(this, QStringLiteral("Project valid"),
                                 QStringLiteral("Merged configuration is valid. Resources: %1 · Operations: %2 · Data sources: %3")
                                     .arg(payload.value(QStringLiteral("resources")).toInt())
                                     .arg(payload.value(QStringLiteral("operations")).toInt())
                                     .arg(payload.value(QStringLiteral("data_sources")).toInt()));
        return;
    }
    if (operation == QStringLiteral("create-project")) {
        showStatusMessage(QStringLiteral("Server created the project."));
        m_api->fetchProjects();
    }
}

void MainWindow::handleApiError(const QString &operation, int statusCode, const QString &message,
                                const QString &category, const QString &technicalDetails,
                                bool outcomeUncertain)
{
    if (category == QStringLiteral("canceled") || operation == QStringLiteral("auth-logout")
        || m_disconnecting) {
        return;
    }
    if (operation.startsWith(QStringLiteral("team-")) && statusCode != 401) {
        return; // Team Workspace displays its own errors without interrupting editing.
    }
    if ((operation == QStringLiteral("capabilities") || operation.startsWith(QStringLiteral("capabilities:")))
        && statusCode != 401) {
        if (operation.startsWith(QStringLiteral("capabilities:"))
            && operation != QStringLiteral("capabilities:%1").arg(m_currentProject)) { return; }
        if (operation == QStringLiteral("capabilities") && !m_currentProject.isEmpty()) { return; }
        m_teamWorkspace->setAvailabilityError(QStringLiteral("Could not load server features (HTTP %1): %2").arg(statusCode).arg(message));
        showStatusMessage(QStringLiteral("Server features could not be loaded. Use Retry in Spaces & calls."), 7000);
        return;
    }
    if (operation == QStringLiteral("auth-setup") && (outcomeUncertain || statusCode == 409)) {
        m_api->clearSession();
        m_connectionLabel->setText(QStringLiteral("●  Verifying founder setup…"));
        m_connectionLabel->setObjectName(QStringLiteral("connectionPending"));
        m_api->fetchSetupStatus(QStringLiteral("setup-reconcile"));
        return;
    }
    if (operation.startsWith(QStringLiteral("auth-"))) {
        m_api->clearSession();
        m_connectionLabel->setText(QStringLiteral("●  Authentication failed"));
        m_connectionLabel->setObjectName(QStringLiteral("connectionOffline"));
        m_teamWorkspace->reset();
    }
    if (statusCode == 401 && !operation.startsWith(QStringLiteral("auth-"))) {
        if (m_sessionExpired) { return; }
        m_sessionExpired = true;
        m_api->cancelActiveRequests();
        m_connectionLabel->setText(QStringLiteral("●  Session expired"));
        m_connectionLabel->setObjectName(QStringLiteral("connectionOffline"));
        m_teamWorkspace->reset();
        m_saveAction->setEnabled(false);
        m_createAction->setEnabled(false);
        m_graphCreateAction->setEnabled(false);
        updateConnectionActions();
    }
    if (operation.startsWith(QStringLiteral("save:"))) {
        m_saveAction->setEnabled(m_api->isConfigured() && !m_policyReadOnly && !m_currentDocument.isEmpty());
        if (statusCode == 409 && !m_pendingSaveProject.isEmpty()) {
            m_api->fetchDocument(m_pendingSaveProject, m_pendingSaveDocument,
                                 QStringLiteral("conflict-document"));
            return;
        }
        if (outcomeUncertain && !m_pendingSaveProject.isEmpty()) {
            m_connectionLabel->setText(QStringLiteral("●  Verifying save outcome…"));
            m_api->fetchDocument(m_pendingSaveProject, m_pendingSaveDocument,
                                 QStringLiteral("reconcile-save"));
            return;
        }
    }
    const bool reconnect = statusCode == 401 || operation.startsWith(QStringLiteral("auth-"));
    showApiErrorDialog(operation, statusCode, message, category, technicalDetails, reconnect);
}

void MainWindow::showApiErrorDialog(const QString &operation, int statusCode,
                                    const QString &message, const QString &category,
                                    const QString &technicalDetails, bool offerReconnect)
{
    QString title = QStringLiteral("Editor request failed");
    QString action = QStringLiteral("Review the details and try the operation again when the cause is resolved.");
    QMessageBox::Icon icon = QMessageBox::Warning;
    if (category == QStringLiteral("timeout")) {
        title = QStringLiteral("Request timed out");
        action = QStringLiteral("Check server health or increase the bounded timeout in Settings → Network.");
    } else if (category == QStringLiteral("tls")) {
        title = QStringLiteral("TLS validation failed");
        action = QStringLiteral("The connection was aborted. Fix the server certificate or trust chain; certificate errors cannot be ignored.");
        icon = QMessageBox::Critical;
    } else if (category == QStringLiteral("authentication")) {
        title = QStringLiteral("Authentication required");
        action = QStringLiteral("Sign in again. Your open document and unsaved buffer have been preserved.");
    } else if (category == QStringLiteral("authorization")) {
        title = QStringLiteral("Server policy blocked the request");
        action = QStringLiteral("Ask a founder to verify your role and document scope.");
    } else if (category == QStringLiteral("validation")) {
        title = QStringLiteral("Request validation failed");
        action = QStringLiteral("Correct the indicated value or document, then retry.");
    } else if (category == QStringLiteral("server")) {
        title = QStringLiteral("Forge server error");
        action = QStringLiteral("Check the server logs and request ID, then retry when the service is healthy.");
    }
    if (statusCode == 403 && message.contains(QStringLiteral("Client IP"), Qt::CaseInsensitive)) {
        action = QStringLiteral(
            "The server rejected the effective client/source address. Verify EDITOR_ALLOWED_IPS and, only when a trusted reverse proxy is in use, EDITOR_TRUSTED_PROXY_CIDRS. Do not broaden either list without reviewing the network boundary.");
    }

    const auto details = QStringLiteral("Operation: %1\nCategory: %2\nHTTP status: %3\n%4")
                             .arg(operation, category)
                             .arg(statusCode)
                             .arg(technicalDetails.isEmpty()
                                      ? QStringLiteral("No additional technical details were provided.")
                                      : technicalDetails);
    QMessageBox box(icon, title, message, QMessageBox::Close, this);
    box.setInformativeText(action);
    box.setDetailedText(details);
    auto *copy = box.addButton(QStringLiteral("Copy details"), QMessageBox::ActionRole);
    QPushButton *reconnect = nullptr;
    if (offerReconnect && m_api->hasServer()) {
        reconnect = box.addButton(QStringLiteral("Sign in again…"), QMessageBox::ActionRole);
    }
    box.exec();
    if (box.clickedButton() == copy) {
        QApplication::clipboard()->setText(
            QStringLiteral("%1\n%2\n\n%3\n\n%4").arg(title, message, action, details));
        showStatusMessage(QStringLiteral("Sanitized error details copied."), 4000);
    } else if (reconnect != nullptr && box.clickedButton() == reconnect) {
        QTimer::singleShot(0, this, &MainWindow::connectToServer);
    }
}

void MainWindow::showSaveConflict(const QJsonObject &serverDocument)
{
    const auto path = serverDocument.value(QStringLiteral("path")).toString();
    const auto serverBytes = serverDocument.value(QStringLiteral("content")).toString().toUtf8();
    const auto serverSha = serverDocument.value(QStringLiteral("sha256")).toString();
    if (path.isEmpty() || path != m_pendingSaveDocument
        || m_currentProject != m_pendingSaveProject || m_currentDocument != path) {
        showApiErrorDialog(QStringLiteral("conflict-document"), 409,
                           QStringLiteral("The server conflict response no longer matches the open document."),
                           QStringLiteral("conflict"),
                           QStringLiteral("The local buffer was left unchanged; reopen the document before resolving the conflict."));
        return;
    }

    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Resolve server document conflict"));
    ForgeEditorUi::resizeToFit(&dialog, QSize(1100, 700));
    auto *root = new QVBoxLayout(&dialog);
    auto *notice = new QLabel(
        QStringLiteral("The server changed after this document was opened. Compare both revisions. Reloading is destructive; choosing local explicitly updates the expected server revision for the next save."),
        &dialog);
    notice->setWordWrap(true);
    notice->setObjectName(QStringLiteral("warningCard"));
    root->addWidget(notice);
    auto *splitter = new QSplitter(Qt::Horizontal, &dialog);
    auto makeRevision = [splitter](const QString &title, const QByteArray &bytes) {
        auto *panel = new QWidget(splitter);
        auto *layout = new QVBoxLayout(panel);
        layout->addWidget(new QLabel(title, panel));
        auto *editor = new QPlainTextEdit(panel);
        editor->setReadOnly(true);
        editor->setLineWrapMode(QPlainTextEdit::NoWrap);
        editor->setPlainText(QString::fromUtf8(bytes));
        layout->addWidget(editor, 1);
        return editor;
    };
    const auto localBytes = currentEditorBytes();
    auto *localEditor = makeRevision(QStringLiteral("Local unsaved revision"), localBytes);
    makeRevision(QStringLiteral("Current server revision"), serverBytes);
    root->addWidget(splitter, 1);
    auto *buttons = new QHBoxLayout;
    auto *copyLocal = new QPushButton(QStringLiteral("Copy local revision"), &dialog);
    auto *keepLocal = new QPushButton(QStringLiteral("Use local on next save"), &dialog);
    auto *reload = new QPushButton(QStringLiteral("Reload server revision"), &dialog);
    auto *cancel = new QPushButton(QStringLiteral("Cancel"), &dialog);
    buttons->addWidget(copyLocal);
    buttons->addStretch();
    buttons->addWidget(keepLocal);
    buttons->addWidget(reload);
    buttons->addWidget(cancel);
    root->addLayout(buttons);
    connect(copyLocal, &QPushButton::clicked, &dialog, [localEditor] {
        QApplication::clipboard()->setText(localEditor->toPlainText());
    });
    connect(keepLocal, &QPushButton::clicked, &dialog, [this, &dialog, serverSha] {
        m_currentSha256 = serverSha;
        setDirty(true);
        dialog.done(2);
    });
    connect(reload, &QPushButton::clicked, &dialog, [this, &dialog, path, serverBytes, serverSha] {
        loadDocument(path, serverBytes, serverSha);
        dialog.done(3);
    });
    connect(cancel, &QPushButton::clicked, &dialog, &QDialog::reject);
    const int result = dialog.exec();
    if (result == 2) {
        showStatusMessage(QStringLiteral("Local revision preserved. The next save will target the reviewed server revision."), 8000);
    } else if (result == 3) {
        showStatusMessage(QStringLiteral("Reloaded the current server revision."), 5000);
    }
    m_pendingSaveProject.clear();
    m_pendingSaveDocument.clear();
    m_pendingSaveDigest.clear();
    m_saveAction->setEnabled(m_api->isConfigured() && !m_policyReadOnly && !m_currentDocument.isEmpty());
}

void MainWindow::updatePolicyPanel()
{
    if (!m_remoteMode) {
        updateConnectionActions();
        m_policyLabel->setText(QStringLiteral("Local files · atomic save\nHooks + graphs: enabled locally\nFull schema: use Forge CLI/server"));
        return;
    }
    updateConnectionActions();
    m_policyLabel->setText(QStringLiteral("%1\nCreate projects: %2\nPython hooks: %3\nOperation graphs: %4\nDocument limit: %5 KiB")
                               .arg(m_policyReadOnly ? QStringLiteral("Read only") : QStringLiteral("Validated writes"),
                                    m_policyCreate ? QStringLiteral("allowed") : QStringLiteral("blocked"),
                                    m_policyHooks ? QStringLiteral("allowed") : QStringLiteral("blocked"),
                                    m_policyGraphs ? QStringLiteral("allowed") : QStringLiteral("blocked"))
                               .arg(m_policyMaxBytes / 1024));
    m_saveAction->setEnabled(!m_currentDocument.isEmpty() && !m_policyReadOnly);
    if (m_graphCreateAction != nullptr) {
        m_graphCreateAction->setEnabled(m_policyGraphs && !m_policyReadOnly);
    }
}

void MainWindow::setDirty(bool dirty)
{
    if (dirty) { ++m_editGeneration; }
    m_dirty = dirty;
    setWindowModified(dirty);
    const auto title = QStringLiteral("JSON API Forge Editor v0.5.2[*]");
    setWindowTitle(title);
    if (!m_currentDocument.isEmpty()) {
        auto text = QStringLiteral("%1  /  %2  ·  %3").arg(currentModeName(m_remoteMode), m_currentProject, m_currentDocument);
        if (dirty) {
            text += QStringLiteral("  ● unsaved");
        }
        m_breadcrumb->setText(text);
        m_breadcrumb->setToolTip(text);
    }
}

bool MainWindow::confirmDiscard()
{
    if (!m_dirty || !m_preferences.confirmDiscard) {
        return true;
    }
    const auto answer = QMessageBox::question(this, QStringLiteral("Unsaved changes"),
                                              QStringLiteral("Discard unsaved changes to %1?").arg(m_currentDocument),
                                              QMessageBox::Discard | QMessageBox::Cancel, QMessageBox::Cancel);
    return answer == QMessageBox::Discard;
}

void MainWindow::importPluginZip()
{
    const auto archivePath = QFileDialog::getOpenFileName(
        this, QStringLiteral("Import plugin from ZIP"), QString(),
        QStringLiteral("Plugin ZIP archives (*.zip)"));
    if (archivePath.isEmpty()) {
        return;
    }
    const auto confirmation = QMessageBox::warning(
        this, QStringLiteral("Import native plugin"),
        QStringLiteral("Native plugins run with your desktop account's full privileges. The ZIP will be checked for path traversal, links, size abuse, manifest integrity and library SHA-256. It will be imported disabled; review it before enabling."),
        QMessageBox::Open | QMessageBox::Cancel, QMessageBox::Cancel);
    if (confirmation != QMessageBox::Open) {
        return;
    }
    const auto imported = PluginManager::importZip(archivePath, m_userPluginDirectory);
    if (!imported.success) {
        QMessageBox::critical(this, QStringLiteral("Plugin import rejected"), imported.error);
        return;
    }

    QSettings settings;
    auto enabled = settings.value(QStringLiteral("plugins/enabledIds")).toStringList();
    enabled.removeAll(imported.pluginId);
    settings.setValue(QStringLiteral("plugins/enabledIds"), enabled);
    const auto manage = QMessageBox::information(
        this, QStringLiteral("Plugin imported disabled"),
        QStringLiteral("%1 %2 was verified and imported. It has not been loaded or enabled. Open Plugin Manager now to review its manifest, permissions and status?")
            .arg(imported.pluginName, imported.version),
        QMessageBox::Yes | QMessageBox::No, QMessageBox::No);
    if (manage == QMessageBox::Yes) {
        managePlugins();
    }
}

void MainWindow::managePlugins()
{
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Manage native plugins"));
    ForgeEditorUi::resizeToFit(&dialog, QSize(780, 480));
    auto *layout = new QVBoxLayout(&dialog);
    auto *warning = new QLabel(
        QStringLiteral("Native plugins execute with your desktop account's full privileges. Enable only plugins you obtained and reviewed from a trusted source. The editor does not auto-enable discovered code."),
        &dialog);
    warning->setWordWrap(true);
    warning->setObjectName(QStringLiteral("warningCard"));
    layout->addWidget(warning);
    auto *tree = new QTreeWidget(&dialog);
    tree->setColumnCount(6);
    tree->setHeaderLabels({QStringLiteral("Enabled"), QStringLiteral("Plugin"), QStringLiteral("Version"), QStringLiteral("Status"),
                           QStringLiteral("Permissions"), QStringLiteral("Manifest")});
    tree->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    tree->header()->setSectionResizeMode(5, QHeaderView::Stretch);
    const auto enabled = enabledPluginIds();
    for (const auto &descriptor : m_pluginManager->discover()) {
        auto *item = new QTreeWidgetItem(tree, {QString(), descriptor.name, descriptor.version,
                                                descriptor.error.isEmpty() ? QStringLiteral("Compatible") : descriptor.error,
                                                descriptor.permissions.isEmpty() ? QStringLiteral("none declared")
                                                                                 : descriptor.permissions.join(QStringLiteral(", ")),
                                                descriptor.manifestPath});
        item->setData(0, Qt::UserRole, descriptor.id);
        item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
        item->setCheckState(0, enabled.contains(descriptor.id) ? Qt::Checked : Qt::Unchecked);
        if (!descriptor.error.isEmpty()) {
            item->setDisabled(true);
        }
    }
    layout->addWidget(tree);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Cancel | QDialogButtonBox::Save, &dialog);
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) {
        return;
    }
    QStringList ids;
    for (int index = 0; index < tree->topLevelItemCount(); ++index) {
        const auto *item = tree->topLevelItem(index);
        if (!item->isDisabled() && item->checkState(0) == Qt::Checked) {
            ids.append(item->data(0, Qt::UserRole).toString());
        }
    }
    QSettings().setValue(QStringLiteral("plugins/enabledIds"), ids);
    reloadPlugins();
}

void MainWindow::browsePluginCatalog()
{
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Forge-backed plugin catalog"));
    ForgeEditorUi::resizeToFit(&dialog, QSize(980, 650));
    auto *layout = new QVBoxLayout(&dialog);
    auto *notice = new QLabel(
        QStringLiteral("Catalog metadata is read through a normal JSON API Forge resource. The Editor never downloads, installs, enables, or executes native code automatically. Review publisher, permissions, HTTPS package URL and SHA-256 before a separate manual install."),
        &dialog);
    notice->setObjectName(QStringLiteral("warningCard"));
    notice->setWordWrap(true);
    layout->addWidget(notice);

    QSettings settings;
    auto *server = new QLineEdit(settings.value(QStringLiteral("pluginCatalog/server"),
                                                m_api->isConfigured() ? m_api->serverUrl().toString()
                                                                      : QStringLiteral("https://forge.example.com"))
                                     .toString(),
                                 &dialog);
    auto *project = new QLineEdit(settings.value(QStringLiteral("pluginCatalog/project"), QStringLiteral("editor-plugin-registry"))
                                      .toString(),
                                  &dialog);
    auto *resource = new QLineEdit(settings.value(QStringLiteral("pluginCatalog/resource"), QStringLiteral("editor/plugins"))
                                       .toString(),
                                   &dialog);
    auto *apiKey = new QLineEdit(&dialog);
    apiKey->setEchoMode(QLineEdit::Password);
    apiKey->setPlaceholderText(QStringLiteral("Read-only Forge API key (not stored)"));
    auto *allowHttp = new QCheckBox(QStringLiteral("Allow plain HTTP for loopback development only"), &dialog);
    auto *form = new QFormLayout;
    form->addRow(QStringLiteral("Forge server"), server);
    form->addRow(QStringLiteral("Catalog project"), project);
    form->addRow(QStringLiteral("Catalog resource"), resource);
    form->addRow(QStringLiteral("API key"), apiKey);
    form->addRow(QString(), allowHttp);
    layout->addLayout(form);

    auto *status = new QLabel(QStringLiteral("Enter a read-only catalog key, then fetch up to 100 reviewed releases."), &dialog);
    status->setObjectName(QStringLiteral("policyCard"));
    status->setWordWrap(true);
    layout->addWidget(status);
    auto *tree = new QTreeWidget(&dialog);
    tree->setColumnCount(7);
    tree->setHeaderLabels({QStringLiteral("Plugin"), QStringLiteral("Version"), QStringLiteral("Status"),
                           QStringLiteral("Platform"), QStringLiteral("Publisher"), QStringLiteral("Permissions"),
                           QStringLiteral("SHA-256")});
    tree->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    tree->header()->setSectionResizeMode(5, QHeaderView::Stretch);
    tree->setRootIsDecorated(false);
    tree->setAlternatingRowColors(true);
    layout->addWidget(tree, 1);

    auto *buttonRow = new QHBoxLayout;
    auto *fetch = new QPushButton(QStringLiteral("Fetch from Forge"), &dialog);
    fetch->setObjectName(QStringLiteral("primaryButton"));
    auto *copyUrl = new QPushButton(QStringLiteral("Copy selected package URL"), &dialog);
    copyUrl->setEnabled(false);
    auto *close = new QPushButton(QStringLiteral("Close"), &dialog);
    buttonRow->addWidget(fetch);
    buttonRow->addWidget(copyUrl);
    buttonRow->addStretch();
    buttonRow->addWidget(close);
    layout->addLayout(buttonRow);

    auto *client = new PluginCatalogClient(&dialog);
    connect(fetch, &QPushButton::clicked, &dialog, [=, &settings] {
        tree->clear();
        copyUrl->setEnabled(false);
        fetch->setEnabled(false);
        status->setText(QStringLiteral("Loading catalog through JSON API Forge…"));
        settings.setValue(QStringLiteral("pluginCatalog/server"), server->text().trimmed());
        settings.setValue(QStringLiteral("pluginCatalog/project"), project->text().trimmed());
        settings.setValue(QStringLiteral("pluginCatalog/resource"), resource->text().trimmed());
        client->fetch(QUrl(server->text().trimmed()), apiKey->text().toUtf8(), project->text().trimmed(), resource->text().trimmed(),
                      allowHttp->isChecked());
    });
    connect(client, &PluginCatalogClient::catalogReceived, &dialog, [=](const QJsonArray &items) {
        for (const auto &value : items) {
            const auto item = value.toObject();
            QStringList permissions;
            for (const auto &permission : item.value(QStringLiteral("permissions")).toArray()) {
                permissions.append(permission.toString());
            }
            const auto id = item.value(QStringLiteral("plugin_id")).toString(item.value(QStringLiteral("id")).toString());
            auto *row = new QTreeWidgetItem(tree, {QStringLiteral("%1\n%2").arg(item.value(QStringLiteral("name")).toString(), id),
                                                   item.value(QStringLiteral("version")).toString(),
                                                   item.value(QStringLiteral("status")).toString(QStringLiteral("unknown")),
                                                   item.value(QStringLiteral("platform")).toString(QStringLiteral("any")),
                                                   item.value(QStringLiteral("publisher")).toString(QStringLiteral("unknown")),
                                                   permissions.join(QStringLiteral(", ")),
                                                   item.value(QStringLiteral("sha256")).toString().left(16) + QStringLiteral("…")});
            row->setData(0, Qt::UserRole, item.value(QStringLiteral("download_url")).toString());
            row->setToolTip(0, item.value(QStringLiteral("description")).toString());
            row->setToolTip(6, item.value(QStringLiteral("sha256")).toString());
        }
        fetch->setEnabled(true);
        status->setText(QStringLiteral("Forge returned %1 validated plugin release records. No code was downloaded or enabled.")
                            .arg(items.size()));
    });
    connect(client, &PluginCatalogClient::requestFailed, &dialog, [=](const QString &message) {
        fetch->setEnabled(true);
        status->setText(QStringLiteral("Catalog request failed: %1").arg(message));
    });
    connect(tree, &QTreeWidget::itemSelectionChanged, &dialog, [=] { copyUrl->setEnabled(tree->currentItem() != nullptr); });
    connect(copyUrl, &QPushButton::clicked, &dialog, [=] {
        if (tree->currentItem() != nullptr) {
            QApplication::clipboard()->setText(tree->currentItem()->data(0, Qt::UserRole).toString());
            status->setText(
                QStringLiteral("Package URL copied. Verify the downloaded file against the full SHA-256 before review/install."));
        }
    });
    connect(close, &QPushButton::clicked, &dialog, &QDialog::accept);
    dialog.exec();
    apiKey->clear();
}

QSet<QString> MainWindow::enabledPluginIds() const
{
    const auto values = QSettings().value(QStringLiteral("plugins/enabledIds")).toStringList();
    return QSet<QString>(values.cbegin(), values.cend());
}

void MainWindow::reloadPlugins()
{
    if (m_pluginManager == nullptr) {
        return;
    }
    m_pluginToolBar->clear();
    const auto messages = m_pluginManager->loadEnabled(enabledPluginIds(), this);
    if (!messages.isEmpty()) {
        showStatusMessage(messages.join(QStringLiteral(" · ")), 8000);
    }
    m_pluginToolBar->setVisible(!m_pluginToolBar->actions().isEmpty());
}

void MainWindow::showAbout()
{
    QMessageBox box(this);
    box.setWindowTitle(QStringLiteral("About JSON API Forge Editor"));
    box.setIconPixmap(QPixmap(QStringLiteral(":/branding/logo.png")).scaled(112, 112, Qt::KeepAspectRatio, Qt::SmoothTransformation));
    box.setText(QStringLiteral(
        "<h2>JSON API Forge Editor 0.5.2</h2><p>An Amber Gold + Graphite Gray C++20 / Qt 6 workspace for local and secure remote Forge projects.</p>"
        "<p>Code + graphs + visual configuration · database explorer · ranked team spaces · notes · WebRTC calls · optimistic concurrency · validated atomic saves.</p>"));
    box.exec();
}

void MainWindow::addPaletteComponent(const QString &label, const QString &collection, const QJsonObject &documentTemplate)
{
    m_visualDesigner->addPaletteComponent(label, collection, documentTemplate);
}

void MainWindow::addGraphNodeType(const QString &label, const QString &type, const QJsonObject &defaultProperties)
{
    m_graphEditor->addPaletteNode(label, type, defaultProperties);
}

void MainWindow::addToolAction(QAction *action)
{
    if (action != nullptr) {
        m_pluginToolBar->addAction(action);
        m_pluginToolBar->show();
    }
}

void MainWindow::addDockWidget(Qt::DockWidgetArea area, QDockWidget *dock)
{
    QMainWindow::addDockWidget(area, dock);
}

void MainWindow::showStatusMessage(const QString &message, int timeoutMs)
{
    statusBar()->showMessage(message, timeoutMs);
}

void MainWindow::arrangeTeamDock()
{
    if (m_arrangingTeamDock || m_teamDock == nullptr || !m_teamDock->isVisible()) {
        return;
    }
    const QScopedValueRollback<bool> arranging(m_arrangingTeamDock, true);
    if (m_teamDock->isFloating()) {
        if (centralWidget() != nullptr) {
            centralWidget()->show();
        }
        return;
    }

    constexpr int CompactTeamDockBelow = 1600;
    constexpr int RestoreTeamDockAt = 1680;
    auto area = dockWidgetArea(m_teamDock);
    if (width() < CompactTeamDockBelow && area != Qt::BottomDockWidgetArea) {
        if (centralWidget() != nullptr) {
            centralWidget()->show();
        }
        QMainWindow::addDockWidget(Qt::BottomDockWidgetArea, m_teamDock);
        m_teamDockAutoBottom = true;
        area = Qt::BottomDockWidgetArea;
    } else if (width() >= RestoreTeamDockAt && m_teamDockAutoBottom && area == Qt::BottomDockWidgetArea) {
        if (centralWidget() != nullptr) {
            centralWidget()->show();
        }
        QMainWindow::addDockWidget(Qt::RightDockWidgetArea, m_teamDock);
        m_teamDockAutoBottom = false;
        area = Qt::RightDockWidgetArea;
    } else if (area == Qt::BottomDockWidgetArea && width() < CompactTeamDockBelow) {
        if (centralWidget() != nullptr) {
            centralWidget()->show();
        }
    } else if (centralWidget() != nullptr) {
        centralWidget()->show();
    }

    if (area == Qt::BottomDockWidgetArea) {
        resizeDocks({m_teamDock}, {qBound(220, height() * 3 / 5, 420)}, Qt::Vertical);
    } else {
        resizeDocks({m_teamDock}, {qBound(340, width() / 2, 660)}, Qt::Horizontal);
    }
}

void MainWindow::closeEvent(QCloseEvent *event)
{
    if (confirmDiscard()) {
        if (m_persistWindowLayout && m_preferences.restoreWindowLayout) {
            QSettings settings;
            settings.setValue(QStringLiteral("window/geometry"), saveGeometry());
            settings.setValue(QStringLiteral("window/state-v1"), saveState(1));
            settings.setValue(QStringLiteral("window/sidebarExpanded"),
                              m_sidebarAutoCollapsed || m_sidebarExpanded);
        }
        event->accept();
    } else {
        event->ignore();
    }
}

void MainWindow::resizeEvent(QResizeEvent *event)
{
    QMainWindow::resizeEvent(event);
    if (m_sidebar == nullptr) {
        return;
    }

    constexpr int CollapseSidebarBelow = 900;
    constexpr int RestoreSidebarAt = 960;
    const int windowWidth = event->size().width();
    if (windowWidth < CollapseSidebarBelow && m_sidebarExpanded) {
        if (m_sidebarAnimation != nullptr) {
            m_sidebarAnimation->stop();
            m_sidebarAnimation->deleteLater();
            m_sidebarAnimation = nullptr;
        }
        m_sidebarExpanded = false;
        m_sidebarAutoCollapsed = true;
        applySidebarState();
    } else if (windowWidth >= RestoreSidebarAt && m_sidebarAutoCollapsed) {
        m_sidebarExpanded = true;
        m_sidebarAutoCollapsed = false;
        applySidebarState();
    }

    if (windowWidth < 1100 && m_pythonDock != nullptr && m_teamDock != nullptr
        && m_pythonDock->isVisible() && m_teamDock->isVisible()) {
        tabifyDockWidget(m_pythonDock, m_teamDock);
        m_teamDock->raise();
    }
    arrangeTeamDock();
}
