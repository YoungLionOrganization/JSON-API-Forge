#pragma once

#include <QJsonArray>
#include <QJsonObject>
#include <QWidget>
#include <QHash>
#include <QSet>
#include <QPointer>

class ApiClient;
class QComboBox;
class QLabel;
class QLineEdit;
class QListWidget;
class QTableWidget;
class QTextEdit;
class QTimer;
class QSpinBox;
class QTabWidget;
class QPushButton;
class QTreeWidget;
class QUrl;
class QDialog;
class ChatView;
class ChatComposer;
class QProgressBar;

class TeamWorkspace final : public QWidget {
    Q_OBJECT

public:
    explicit TeamWorkspace(ApiClient *api, QWidget *parent = nullptr);
    void setProject(const QString &project);
    void setCapabilities(const QJsonObject &capabilities);
    void setAvailabilityError(const QString &message);
    void refreshAll();
    void reset();

signals:
    void statusMessage(const QString &message);
    void retryConnectionRequested();

private slots:
    void handleJson(const QString &operation, const QJsonObject &payload);
    void handleError(const QString &operation, int statusCode, const QString &message);
    void selectArea();
    void sendMessage();
    void createArea();
    void editProfile();
    void createRole();
    void manageMember();
    void createInvitation();
    void uploadAttachment();
    void downloadAttachment();
    void saveNote();
    void selectDatabaseTable();
    void startAudioCall();
    void startVideoCall();

private:
    void buildTeamTab(QWidget *tab);
    void buildSpacesTab(QWidget *tab);
    void buildDatabaseTab(QWidget *tab);
    void buildNotesTab(QWidget *tab);
    void buildAuditTab(QWidget *tab);
    void openCall(const QUrl &url);
    void shareFile(const QString &path);
    void downloadFile(const QString &id);
    void joinCall();
    void startCall(const QString &mode);
    void refreshSpaceExtras();
    [[nodiscard]] QString currentAreaId() const;
    [[nodiscard]] QString projectScope() const;
    [[nodiscard]] bool permits(const QString &permission) const;
    void updateActions();
    void refreshProject();
    void requestMessages();

    ApiClient *m_api = nullptr;
    QLabel *m_profile = nullptr;
    QLabel *m_projectLabel = nullptr;
    QTreeWidget *m_members = nullptr;
    QTreeWidget *m_roles = nullptr;
    QListWidget *m_areas = nullptr;
    ChatView *m_messages = nullptr;
    ChatComposer *m_message = nullptr;
    QJsonArray m_messageRecords;
    QJsonArray m_attachmentRecords;
    QJsonArray m_callRecords;
    QString m_callsLoading;
    QPointer<QDialog> m_callWindow;
    bool m_browserCallNext = false;
    QLabel *m_spaceTitle = nullptr;
    QLabel *m_transferStatus = nullptr;
    QProgressBar *m_transferProgress = nullptr;
    QPushButton *m_cancelTransfer = nullptr;
    QComboBox *m_activeCalls = nullptr;
    QPushButton *m_joinCall = nullptr;
    QString m_pendingCallArea;
    QString m_pendingCallId;
    QString m_pendingUploadArea;
    bool m_transferBusy = false;
    bool m_callDiscovery = false;
    bool m_extrasLoading = false;
    QTreeWidget *m_attachments = nullptr;
    QTreeWidget *m_databaseTree = nullptr;
    QTableWidget *m_rows = nullptr;
    QTreeWidget *m_notes = nullptr;
    QLineEdit *m_noteTitle = nullptr;
    QTextEdit *m_noteBody = nullptr;
    QComboBox *m_noteVisibility = nullptr;
    QSpinBox *m_noteMinimumRank = nullptr;
    QLabel *m_noteRankLabel = nullptr;
    QTreeWidget *m_audit = nullptr;
    QTimer *m_poll = nullptr;
    QTabWidget *m_tabs = nullptr;
    QLabel *m_spaceStatus = nullptr;
    QLabel *m_spaceAvailability = nullptr;
    QPushButton *m_retryConnection = nullptr;
    QLabel *m_feedback = nullptr;
    QPushButton *m_sendButton = nullptr;
    QPushButton *m_noteSaveButton = nullptr;
    QSet<QString> m_permissions;
    QHash<QString, QString> m_messageDrafts;
    QString m_selectedArea;
    QString m_pendingMessageArea;
    QString m_pendingMessageBody;
    QString m_messagesLoading;
    QString m_messagesRefreshNeeded;
    QString m_pendingNoteProject;
    QString m_pendingNoteTitle;
    QString m_pendingNoteBody;
    QString m_pendingNoteVisibility;
    int m_pendingNoteRank = 0;
    QString m_selectedRowsOperation;
    QString m_project;
    QJsonObject m_profileRecord;
    QJsonArray m_memberRecords;
    QJsonArray m_roleRecords;
    QJsonArray m_permissionCatalog;
    qsizetype m_maxAttachmentBytes = 16 * 1024 * 1024;
    bool m_databaseEnabled = false;
    bool m_collaborationEnabled = false;
    bool m_callsEnabled = false;
    bool m_capabilitiesLoaded = false;
    bool m_collaborationAdvertised = false;
    QString m_availabilityError;
    int m_rank = 0;
};
