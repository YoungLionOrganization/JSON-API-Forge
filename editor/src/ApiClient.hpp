#pragma once

#include <QByteArray>
#include <QJsonObject>
#include <QList>
#include <QNetworkAccessManager>
#include <QObject>
#include <QPair>
#include <QUrl>
#include <QUrlQuery>

#include <functional>

class QNetworkReply;
struct EditorPreferences;

class ApiClient final : public QObject {
    Q_OBJECT

public:
    explicit ApiClient(QObject *parent = nullptr);
    ~ApiClient() override;

    bool configureServer(const QUrl &serverUrl, bool allowInsecureHttp, QString *errorMessage);
    bool configure(const QUrl &serverUrl, const QByteArray &sessionToken, bool allowInsecureHttp,
                   QString *errorMessage);
    bool setSessionToken(const QByteArray &sessionToken, QString *errorMessage = nullptr);
    void clearSession();
    void clearCredentials();
    void cancelActiveRequests();
    void applyPreferences(const EditorPreferences &preferences);
    [[nodiscard]] bool hasServer() const;
    [[nodiscard]] bool isConfigured() const;
    [[nodiscard]] QUrl serverUrl() const;

    void login(const QString &username, const QString &password);
    void registerMember(const QString &invitation, const QString &username, const QString &password,
                        const QString &displayName);
    void setupFounder(const QByteArray &setupToken, const QString &username, const QString &password,
                      const QString &displayName);
    void fetchSetupStatus(const QString &operation = QStringLiteral("setup-status"));
    void logout();
    void fetchCapabilities(const QString &project = {});
    void fetchProfile(const QString &project = {});
    void updateProfile(const QJsonObject &values, const QString &project = {});
    void fetchProjects();
    void createProject(const QString &directoryName, const QString &slug);
    void fetchDocuments(const QString &project);
    void fetchDocument(const QString &project, const QString &documentPath,
                       const QString &operation = {});
    void saveDocument(const QString &project, const QString &documentPath, const QByteArray &content,
                      const QString &expectedSha256);
    void validateProject(const QString &project);

    void fetchMembers();
    void fetchRoles();
    void createRole(const QString &name, int rank, const QJsonArray &permissions,
                    const QJsonArray &documentAllow, const QJsonArray &documentDeny,
                    const QJsonArray &databaseAllow);
    void updateMember(const QString &userId, const QJsonArray &memberships, bool active);
    void createInvitation(const QString &roleId, const QString &project, int expiresHours);
    void fetchAreas(const QString &project);
    void createArea(const QString &project, const QString &name, const QString &description,
                    const QString &visibility, int minimumRank);
    void fetchMessages(const QString &areaId);
    void postMessage(const QString &areaId, const QString &body, bool announcement = false);
    void fetchAttachments(const QString &areaId);
    void uploadAttachment(const QString &areaId, const QString &filePath, qsizetype maxBytes);
    void downloadAttachment(const QString &attachmentId, const QString &targetPath, qsizetype maxBytes);
    void fetchNotes(const QString &project);
    void createNote(const QString &project, const QString &areaId, const QString &title, const QString &body,
                    const QString &visibility, int minimumRank = 0);
    void fetchDatabases(const QString &project);
    void fetchDatabaseRows(const QString &project, const QString &alias, const QString &table, int limit = 100,
                           int offset = 0);
    void startCall(const QString &areaId, const QString &mode);
    void createCallTicket(const QString &callId);
    void fetchAudit(const QString &project = {});
    [[nodiscard]] QUrl callClientUrl(const QString &path, const QString &ticket) const;

    static bool normalizeServerUrl(const QUrl &input, bool allowInsecureHttp, QUrl *normalized,
                                   QString *errorMessage);
    static QStringList documentPathSegments(const QString &documentPath,
                                            QString *errorMessage = nullptr);

signals:
    void jsonReceived(const QString &operation, const QJsonObject &payload);
    void requestFailed(const QString &operation, int statusCode, const QString &message);
    void requestFailedDetailed(const QString &operation, int statusCode, const QString &message,
                               const QString &category, const QString &technicalDetails,
                               bool outcomeUncertain);
    void connectionActivityChanged(bool active);
    void tlsRejected(const QString &message);
    void fileDownloaded(const QString &operation, const QString &path);

private:
    enum class RequestProfile { Normal, Authentication };

    void send(const QString &operation, QNetworkAccessManager::Operation method, const QStringList &pathSegments,
              const QJsonObject &body = {}, const QUrlQuery &query = {}, bool authenticationRequired = true,
              const QList<QPair<QByteArray, QByteArray>> &extraHeaders = {},
              RequestProfile profile = RequestProfile::Normal, int retryAttempt = 0);
    void trackJsonReply(QNetworkReply *reply, const QString &operation, bool authenticationRequired,
                        bool mutation, int retryAttempt, const std::function<void()> &retryRequest = {});
    void fail(const QString &operation, int statusCode, const QString &message,
              const QString &category = QStringLiteral("client"),
              const QString &technicalDetails = {}, bool outcomeUncertain = false);
    [[nodiscard]] QUrl endpointFor(const QStringList &pathSegments, const QUrlQuery &query = {}) const;

    QNetworkAccessManager m_network;
    QUrl m_serverUrl;
    QByteArray m_sessionToken;
    qsizetype m_maxResponseBytes = 8 * 1024 * 1024;
    int m_requestTimeoutMs = 60'000;
    int m_authenticationTimeoutMs = 120'000;
    int m_uploadTimeoutMs = 300'000;
    int m_downloadTimeoutMs = 300'000;
    int m_safeGetRetries = 1;
    int m_retryBaseDelayMs = 750;
    int m_activeRequests = 0;
    quint64 m_requestGeneration = 0;
};
