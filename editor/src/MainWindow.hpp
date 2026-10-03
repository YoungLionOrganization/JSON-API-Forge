#pragma once

#include "ApiClient.hpp"
#include "EditorSettings.hpp"
#include "PluginManager.hpp"

#include <QJsonObject>
#include <QMainWindow>
#include <QSet>

class QAction;
class CodeEditor;
class QLabel;
class QListWidget;
class NodeGraphEditor;
class QProgressBar;
class QPropertyAnimation;
class QVariantAnimation;
class QResizeEvent;
class PythonSdkPanel;
class QStackedWidget;
class QSplitter;
class QToolBar;
class QToolButton;
class QTreeWidget;
class TeamWorkspace;
class VisualDesigner;

class MainWindow final : public QMainWindow, public ForgeEditor::EditorHost {
    Q_OBJECT

public:
    explicit MainWindow(QWidget *parent = nullptr, bool restoreLayout = true);
    ~MainWindow() override;
    void showGraphPreview();
    void showTeamPreview();
    void showVisualPreview();

    void addPaletteComponent(const QString &label, const QString &collection, const QJsonObject &documentTemplate) override;
    void addGraphNodeType(const QString &label, const QString &type, const QJsonObject &defaultProperties) override;
    void addToolAction(QAction *action) override;
    void addDockWidget(Qt::DockWidgetArea area, QDockWidget *dock) override;
    void showStatusMessage(const QString &message, int timeoutMs = 4000) override;

protected:
    void closeEvent(QCloseEvent *event) override;
    void resizeEvent(QResizeEvent *event) override;

private slots:
    void connectToServer();
    void disconnectServer();
    void openLocalWorkspace();
    void selectProject();
    void openSelectedDocument();
    void saveDocument();
    void validateProject();
    void createProject();
    void createFromTemplate();
    void showCodeMode();
    void showVisualMode();
    void showGraphMode();
    void createGraph();
    void toggleSidebar();
    void importPluginZip();
    void managePlugins();
    void browsePluginCatalog();
    void showSettings();
    void showAbout();
    void handleApiJson(const QString &operation, const QJsonObject &payload);
    void handleApiError(const QString &operation, int statusCode, const QString &message,
                        const QString &category, const QString &technicalDetails,
                        bool outcomeUncertain);

private:
    friend class EditorUiTests;
    void buildInterface();
    void buildActions();
    void applyStyle();
    void populateLocalProjects(const QString &rootPath);
    void populateLocalDocuments(const QString &projectPath);
    void loadDocument(const QString &path, const QByteArray &content, const QString &sha256);
    void setDirty(bool dirty);
    bool confirmDiscard();
    bool prepareCurrentJson(QJsonObject *object, QByteArray *bytes);
    [[nodiscard]] QByteArray currentEditorBytes() const;
    void applyPreferences();
    void showApiErrorDialog(const QString &operation, int statusCode, const QString &message,
                            const QString &category, const QString &technicalDetails,
                            bool offerReconnect = false);
    void showSaveConflict(const QJsonObject &serverDocument);
    void animateWorkspace(QWidget *widget);
    void showWelcome();
    void updatePolicyPanel();
    void arrangeTeamDock();
    void applySidebarState();
    void updateConnectionActions();
    void reloadPlugins();
    void restoreWindowLayout();
    [[nodiscard]] QSet<QString> enabledPluginIds() const;

    ApiClient *m_api = nullptr;
    PluginManager *m_pluginManager = nullptr;
    QSplitter *m_rootSplitter = nullptr;
    QWidget *m_sidebar = nullptr;
    QToolButton *m_sidebarButton = nullptr;
    QToolButton *m_disconnectButton = nullptr;
    QAction *m_disconnectAction = nullptr;
    QListWidget *m_projects = nullptr;
    QTreeWidget *m_documents = nullptr;
    QLabel *m_connectionLabel = nullptr;
    QLabel *m_breadcrumb = nullptr;
    QLabel *m_policyLabel = nullptr;
    QProgressBar *m_activity = nullptr;
    CodeEditor *m_codeEditor = nullptr;
    VisualDesigner *m_visualDesigner = nullptr;
    NodeGraphEditor *m_graphEditor = nullptr;
    QStackedWidget *m_workspace = nullptr;
    QWidget *m_welcomePage = nullptr;
    QToolButton *m_codeButton = nullptr;
    QToolButton *m_visualButton = nullptr;
    QToolButton *m_graphButton = nullptr;
    QToolBar *m_pluginToolBar = nullptr;
    QDockWidget *m_pythonDock = nullptr;
    PythonSdkPanel *m_pythonPanel = nullptr;
    QDockWidget *m_teamDock = nullptr;
    TeamWorkspace *m_teamWorkspace = nullptr;
    QVariantAnimation *m_sidebarAnimation = nullptr;

    QAction *m_saveAction = nullptr;
    QAction *m_validateAction = nullptr;
    QAction *m_createAction = nullptr;
    QAction *m_graphCreateAction = nullptr;

    QString m_workspaceRoot;
    QString m_currentProject;
    QString m_currentProjectPath;
    QString m_currentDocument;
    QString m_currentSha256;
    QString m_pendingOpenOperation;
    quint64 m_editGeneration = 0;
    quint64 m_pendingOpenGeneration = 0;
    int m_sidebarWidth = 272;
    bool m_disconnecting = false;
    bool m_sessionExpired = false;
    bool m_remoteMode = false;
    bool m_dirty = false;
    bool m_updatingEditor = false;
    bool m_sidebarExpanded = true;
    bool m_sidebarAutoCollapsed = false;
    bool m_arrangingTeamDock = false;
    bool m_teamDockAutoBottom = false;
    bool m_persistWindowLayout = true;
    bool m_reopenSignIn = false;

    EditorPreferences m_preferences;
    QString m_pendingSetupUsername;
    QString m_pendingSaveProject;
    QString m_pendingSaveDocument;
    QString m_pendingSaveDigest;
    QString m_userPluginDirectory;

    bool m_policyReadOnly = true;
    bool m_policyCreate = false;
    bool m_policyHooks = false;
    bool m_policyGraphs = false;
    int m_policyMaxBytes = 0;
};
