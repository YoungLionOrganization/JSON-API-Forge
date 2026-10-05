#include "ApiClient.hpp"

#include "DocumentCodec.hpp"
#include "EditorSettings.hpp"

#include <QDir>
#include <QCryptographicHash>
#include <QFile>
#include <QFileInfo>
#include <QHostAddress>
#include <QHttpMultiPart>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QNetworkProxy>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QPointer>
#include <QRunnable>
#include <QRegularExpression>
#include <QSaveFile>
#include <QSharedPointer>
#include <QSslError>
#include <QTimer>

#include <limits>
#include <utility>

#if defined(Q_OS_WIN)
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#elif defined(Q_OS_UNIX)
#include <cerrno>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace {
constexpr auto EditorPrefix = "__forge/editor/v1";

QString responseDetail(const QByteArray &bytes, const QString &fallback)
{
    QJsonParseError parseError;
    const auto document = QJsonDocument::fromJson(bytes, &parseError);
    if (parseError.error == QJsonParseError::NoError && document.isObject()) {
        const auto detail = document.object().value(QStringLiteral("detail"));
        if (detail.isString()) {
            return detail.toString();
        }
        if (!detail.isUndefined()) {
            return QString::fromUtf8(
                QJsonDocument(QJsonObject{{QStringLiteral("detail"), detail}}).toJson(QJsonDocument::Compact));
        }
    }
    const auto text = QString::fromUtf8(bytes.left(2048)).trimmed();
    return text.isEmpty() ? fallback : text;
}

bool validSessionToken(const QByteArray &token)
{
    static const QRegularExpression pattern(
        QStringLiteral(R"(\Ajfe_session_[A-Za-z0-9_-]{40,100}\z)"));
    return pattern.match(QString::fromLatin1(token)).hasMatch();
}

bool validInvitationToken(const QString &token)
{
    static const QRegularExpression pattern(
        QStringLiteral(R"(\Ajfi_[A-Za-z0-9_-]{40,80}\z)"));
    return pattern.match(token).hasMatch();
}

bool validSetupToken(const QByteArray &token)
{
    if (token.size() < 32 || token.size() > 512) {
        return false;
    }
    for (const char character : token) {
        const auto byte = static_cast<unsigned char>(character);
        if (byte < 0x21U || byte > 0x7eU) {
            return false;
        }
    }
    return true;
}

constexpr qsizetype MaximumAttachmentSnapshot = 512 * 1024 * 1024;

bool safeAttachmentSnapshot(const QString &filePath, qsizetype maxBytes, QByteArray *snapshot,
                            QString *errorMessage)
{
    if (snapshot == nullptr || maxBytes < 0 || maxBytes > MaximumAttachmentSnapshot) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The attachment size policy is invalid.");
        }
        return false;
    }

#if defined(Q_OS_WIN)
    const auto nativePath = QDir::toNativeSeparators(filePath);
    const auto handle = CreateFileW(reinterpret_cast<LPCWSTR>(nativePath.utf16()), GENERIC_READ,
                                    FILE_SHARE_READ, nullptr, OPEN_EXISTING,
                                    FILE_ATTRIBUTE_NORMAL | FILE_FLAG_OPEN_REPARSE_POINT
                                        | FILE_FLAG_SEQUENTIAL_SCAN,
                                    nullptr);
    if (handle == INVALID_HANDLE_VALUE) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file could not be opened safely.");
        }
        return false;
    }
    struct HandleGuard final {
        HANDLE value;
        ~HandleGuard() { CloseHandle(value); }
    } guard{handle};

    BY_HANDLE_FILE_INFORMATION before{};
    if (!GetFileInformationByHandle(handle, &before)
        || (before.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)) != 0) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected path is not a regular non-link file.");
        }
        return false;
    }
    const quint64 expectedSize = (static_cast<quint64>(before.nFileSizeHigh) << 32U)
        | static_cast<quint64>(before.nFileSizeLow);
    if (expectedSize > static_cast<quint64>(maxBytes)
        || expectedSize > static_cast<quint64>(std::numeric_limits<qsizetype>::max())) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file exceeds the server attachment limit.");
        }
        return false;
    }

    QByteArray content;
    content.resize(static_cast<qsizetype>(expectedSize));
    quint64 offset = 0;
    while (offset < expectedSize) {
        const auto requestSize = static_cast<DWORD>(qMin<quint64>(
            expectedSize - offset, static_cast<quint64>(std::numeric_limits<DWORD>::max())));
        DWORD received = 0;
        if (!ReadFile(handle, content.data() + static_cast<qsizetype>(offset), requestSize,
                      &received, nullptr)
            || received == 0) {
            if (errorMessage != nullptr) {
                *errorMessage = QStringLiteral("The selected file changed or could not be read completely.");
            }
            return false;
        }
        offset += static_cast<quint64>(received);
    }
    BY_HANDLE_FILE_INFORMATION after{};
    if (!GetFileInformationByHandle(handle, &after)
        || before.dwVolumeSerialNumber != after.dwVolumeSerialNumber
        || before.nFileIndexHigh != after.nFileIndexHigh || before.nFileIndexLow != after.nFileIndexLow
        || before.nFileSizeHigh != after.nFileSizeHigh || before.nFileSizeLow != after.nFileSizeLow
        || CompareFileTime(&before.ftLastWriteTime, &after.ftLastWriteTime) != 0) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file changed while it was being read.");
        }
        return false;
    }
    *snapshot = std::move(content);
    return true;
#elif defined(Q_OS_UNIX)
    auto flags = O_RDONLY;
#ifdef O_CLOEXEC
    flags |= O_CLOEXEC;
#endif
#ifdef O_NOFOLLOW
    flags |= O_NOFOLLOW;
#endif
    const auto encodedPath = QFile::encodeName(filePath);
    const int descriptor = ::open(encodedPath.constData(), flags);
    if (descriptor < 0) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file could not be opened safely.");
        }
        return false;
    }
    struct DescriptorGuard final {
        int value;
        ~DescriptorGuard() { ::close(value); }
    } guard{descriptor};

    struct stat before {};
    if (::fstat(descriptor, &before) != 0 || !S_ISREG(before.st_mode) || before.st_size < 0
        || static_cast<quint64>(before.st_size) > static_cast<quint64>(maxBytes)) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected path is not a bounded regular file.");
        }
        return false;
    }

    QByteArray content;
    content.reserve(static_cast<qsizetype>(before.st_size));
    char buffer[64 * 1024];
    while (true) {
        ssize_t received = -1;
        do {
            received = ::read(descriptor, buffer, sizeof(buffer));
        } while (received < 0 && errno == EINTR);
        if (received < 0) {
            if (errorMessage != nullptr) {
                *errorMessage = QStringLiteral("The selected file could not be read completely.");
            }
            return false;
        }
        if (received == 0) {
            break;
        }
        const auto count = static_cast<qsizetype>(received);
        if (content.size() > maxBytes - count) {
            if (errorMessage != nullptr) {
                *errorMessage = QStringLiteral("The selected file exceeds the server attachment limit.");
            }
            return false;
        }
        content.append(buffer, count);
    }

    struct stat after {};
    bool unchanged = ::fstat(descriptor, &after) == 0 && before.st_dev == after.st_dev
        && before.st_ino == after.st_ino && before.st_size == after.st_size;
#if defined(Q_OS_DARWIN)
    unchanged = unchanged && before.st_mtimespec.tv_sec == after.st_mtimespec.tv_sec
        && before.st_mtimespec.tv_nsec == after.st_mtimespec.tv_nsec
        && before.st_ctimespec.tv_sec == after.st_ctimespec.tv_sec
        && before.st_ctimespec.tv_nsec == after.st_ctimespec.tv_nsec;
#else
    unchanged = unchanged && before.st_mtim.tv_sec == after.st_mtim.tv_sec
        && before.st_mtim.tv_nsec == after.st_mtim.tv_nsec
        && before.st_ctim.tv_sec == after.st_ctim.tv_sec
        && before.st_ctim.tv_nsec == after.st_ctim.tv_nsec;
#endif
    if (!unchanged || content.size() != static_cast<qsizetype>(before.st_size)) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file changed while it was being read.");
        }
        return false;
    }
    *snapshot = std::move(content);
    return true;
#else
    QFile file(filePath);
    if (!file.open(QIODevice::ReadOnly)) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file could not be opened for reading.");
        }
        return false;
    }
    const auto content = file.read(maxBytes + 1);
    if (content.size() > maxBytes || !file.atEnd()) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The selected file exceeds the server attachment limit.");
        }
        return false;
    }
    *snapshot = content;
    return true;
#endif
}

void hardenRequest(QNetworkRequest &request)
{
    request.setAttribute(QNetworkRequest::RedirectPolicyAttribute, QNetworkRequest::ManualRedirectPolicy);
    request.setAttribute(QNetworkRequest::CacheLoadControlAttribute, QNetworkRequest::AlwaysNetwork);
    request.setAttribute(QNetworkRequest::CacheSaveControlAttribute, false);
    request.setAttribute(QNetworkRequest::CookieLoadControlAttribute, QNetworkRequest::Manual);
    request.setAttribute(QNetworkRequest::CookieSaveControlAttribute, QNetworkRequest::Manual);
    request.setAttribute(QNetworkRequest::AuthenticationReuseAttribute, QNetworkRequest::Manual);
    request.setRawHeader("Cache-Control", "no-store");
}
} // namespace

ApiClient::ApiClient(QObject *parent)
    : QObject(parent)
{
    // A management session must never leak to a desktop's ambient HTTP proxy.
    m_network.setProxy(QNetworkProxy(QNetworkProxy::NoProxy));
    m_filePool.setMaxThreadCount(1);
}

ApiClient::~ApiClient()
{
    clearCredentials();
    m_filePool.waitForDone();
}

bool ApiClient::normalizeServerUrl(const QUrl &input, bool allowInsecureHttp, QUrl *normalized,
                                   QString *errorMessage)
{
    if (!input.isValid() || input.host().isEmpty() || !input.userInfo().isEmpty() || input.hasQuery()
        || input.hasFragment()) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("Enter an absolute server URL without credentials, query or fragment.");
        }
        return false;
    }
    const auto scheme = input.scheme().toLower();
    QHostAddress address;
    const bool loopbackHost = input.host().compare(QStringLiteral("localhost"), Qt::CaseInsensitive) == 0
        || (address.setAddress(input.host()) && address.isLoopback());
    if (scheme != QStringLiteral("https")
        && !(allowInsecureHttp && scheme == QStringLiteral("http") && loopbackHost)) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral(
                "HTTPS is required. Plain HTTP can only be enabled explicitly for a loopback server.");
        }
        return false;
    }
    QUrl value(input);
    value.setScheme(scheme);
    auto path = value.path();
    while (path.endsWith(u'/')) {
        path.chop(1);
    }
    const auto pathParts = path.split(u'/', Qt::KeepEmptyParts);
    if (path.contains(u'\\') || path.contains(QChar::Null) || pathParts.contains(QStringLiteral("."))
        || pathParts.contains(QStringLiteral(".."))) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The server URL base path contains an unsafe segment.");
        }
        return false;
    }
    value.setPath(path);
    if (normalized != nullptr) {
        *normalized = value;
    }
    return true;
}

bool ApiClient::configureServer(const QUrl &serverUrl, bool allowInsecureHttp, QString *errorMessage)
{
    QUrl normalized;
    if (!normalizeServerUrl(serverUrl, allowInsecureHttp, &normalized, errorMessage)) {
        return false;
    }
    clearCredentials();
    m_serverUrl = normalized;
    return true;
}

bool ApiClient::configure(const QUrl &serverUrl, const QByteArray &sessionToken, bool allowInsecureHttp,
                          QString *errorMessage)
{
    if (!configureServer(serverUrl, allowInsecureHttp, errorMessage)) {
        return false;
    }
    if (!setSessionToken(sessionToken, errorMessage)) {
        m_serverUrl.clear();
        return false;
    }
    return true;
}

bool ApiClient::setSessionToken(const QByteArray &sessionToken, QString *errorMessage)
{
    if (!validSessionToken(sessionToken)) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The server returned an invalid Editor session token.");
        }
        return false;
    }
    m_sessionToken.fill('\0');
    m_sessionToken = sessionToken;
    return true;
}

void ApiClient::clearCredentials()
{
    cancelActiveRequests();
    clearSession();
    m_serverUrl.clear();
}

void ApiClient::clearSession()
{
    m_sessionToken.fill('\0');
    m_sessionToken.clear();
}

void ApiClient::cancelActiveRequests()
{
    ++m_requestGeneration;
    for (const auto &job : m_fileJobs) { job->store(true); }
    const auto replies = m_network.findChildren<QNetworkReply *>(QString(),
                                                                 Qt::FindDirectChildrenOnly);
    for (auto *reply : replies) {
        if (reply != nullptr && reply->isRunning() && !reply->property("forgeLogout").toBool()) {
            reply->setProperty("forgeUserCanceled", true);
            reply->abort();
        }
    }
}

void ApiClient::cancelFileTransfers()
{
    for (const auto &job : m_fileJobs) { job->store(true); }
    for (auto *reply : m_network.findChildren<QNetworkReply *>()) {
        if (reply->property("forgeFileTransfer").toBool() && reply->isRunning()) {
            reply->setProperty("forgeUserCanceled", true); reply->abort();
        }
    }
    fail(QStringLiteral("team-attachment-cancel"), 0, QStringLiteral("File transfer canceled."), QStringLiteral("canceled"));
}

void ApiClient::applyPreferences(const EditorPreferences &preferences)
{
    auto value = preferences;
    value.clamp();
    m_requestTimeoutMs = value.requestTimeoutMs;
    m_authenticationTimeoutMs = value.authenticationTimeoutMs;
    m_uploadTimeoutMs = value.uploadTimeoutMs;
    m_downloadTimeoutMs = value.downloadTimeoutMs;
    m_safeGetRetries = value.safeGetRetries;
    m_retryBaseDelayMs = value.retryBaseDelayMs;
    m_maxResponseBytes = static_cast<qsizetype>(value.maxResponseMiB) * 1024 * 1024;
}

bool ApiClient::hasServer() const
{
    return m_serverUrl.isValid();
}

bool ApiClient::isConfigured() const
{
    return hasServer() && validSessionToken(m_sessionToken);
}

QUrl ApiClient::serverUrl() const
{
    return m_serverUrl;
}

QUrl ApiClient::endpointFor(const QStringList &pathSegments, const QUrlQuery &query) const
{
    auto path = m_serverUrl.path();
    path += u'/' + QString::fromLatin1(EditorPrefix);
    for (const auto &segment : pathSegments) {
        if (segment.isEmpty() || segment == QStringLiteral(".") || segment == QStringLiteral("..")
            || segment.contains(u'/') || segment.contains(u'\\') || segment.contains(QChar::Null)) {
            return {};
        }
        path += u'/' + segment;
    }
    QUrl result(m_serverUrl);
    result.setPath(path);
    result.setQuery(query);
    return result;
}

QStringList ApiClient::documentPathSegments(const QString &documentPath, QString *errorMessage)
{
    if (!DocumentCodec::isSafeDocumentPath(documentPath, true) || documentPath.contains(u'%')) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral(
                "The document path is outside the direct app.json, config, hooks or graphs policy.");
        }
        return {};
    }
    const auto segments = documentPath.split(u'/', Qt::KeepEmptyParts);
    if (segments.isEmpty() || segments.size() > 2) {
        if (errorMessage != nullptr) {
            *errorMessage = QStringLiteral("The document path contains an unsafe nested segment.");
        }
        return {};
    }
    return segments;
}

void ApiClient::fail(const QString &operation, int statusCode, const QString &message,
                     const QString &category, const QString &technicalDetails,
                     bool outcomeUncertain)
{
    emit requestFailedDetailed(operation, statusCode, message, category, technicalDetails,
                               outcomeUncertain);
    emit requestFailed(operation, statusCode, message);
}

void ApiClient::send(const QString &operation, QNetworkAccessManager::Operation method,
                     const QStringList &pathSegments, const QJsonObject &body, const QUrlQuery &query,
                     bool authenticationRequired, const QList<QPair<QByteArray, QByteArray>> &extraHeaders,
                     RequestProfile profile, int retryAttempt)
{
    if (!hasServer() || (authenticationRequired && !isConfigured())) {
        fail(operation, 0, QStringLiteral("Connect and sign in to a Forge server first."),
             QStringLiteral("authentication"));
        return;
    }
    const auto endpoint = endpointFor(pathSegments, query);
    if (!endpoint.isValid()) {
        fail(operation, 0, QStringLiteral("The request path contains an unsafe segment."),
             QStringLiteral("validation"));
        return;
    }
    QNetworkRequest request(endpoint);
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    request.setRawHeader("Accept", "application/json");
    if (authenticationRequired) {
        request.setRawHeader("Authorization", QByteArray("Bearer ") + m_sessionToken);
    }
    for (const auto &[name, value] : extraHeaders) {
        request.setRawHeader(name, value);
    }
    hardenRequest(request);
    int timeoutMs = m_requestTimeoutMs;
    if (profile == RequestProfile::Authentication
        || operation.startsWith(QStringLiteral("auth-"))
        || operation.startsWith(QStringLiteral("setup-"))) {
        timeoutMs = m_authenticationTimeoutMs;
    }
    request.setTransferTimeout(timeoutMs);
    const auto encodedBody = body.isEmpty() ? QByteArray() : QJsonDocument(body).toJson(QJsonDocument::Compact);
    QNetworkReply *reply = nullptr;
    switch (method) {
    case QNetworkAccessManager::GetOperation:
        reply = m_network.get(request);
        break;
    case QNetworkAccessManager::PostOperation:
        reply = m_network.post(request, encodedBody);
        break;
    case QNetworkAccessManager::PutOperation:
        reply = m_network.put(request, encodedBody);
        break;
    case QNetworkAccessManager::CustomOperation:
        reply = m_network.sendCustomRequest(request, QByteArray("PATCH"), encodedBody);
        break;
    default:
        fail(operation, 0, QStringLiteral("Unsupported editor HTTP operation."));
        return;
    }
    reply->setProperty("forgeLogout", operation == QStringLiteral("auth-logout"));
    reply->setProperty("forgeBackground", profile == RequestProfile::Background);
    auto *deadline = new QTimer(reply);
    deadline->setSingleShot(true);
    connect(deadline, &QTimer::timeout, reply, [reply] {
        reply->setProperty("forgeTimedOut", true);
        reply->abort();
    });
    reply->setProperty("forgeTimeoutMs", timeoutMs);
    deadline->start(timeoutMs);

    const auto generation = m_requestGeneration;
    std::function<void()> retryRequest;
    if (method == QNetworkAccessManager::GetOperation) {
        retryRequest = [this, operation, method, pathSegments, body, query, authenticationRequired,
                        extraHeaders, profile, retryAttempt, generation] {
            if (generation != m_requestGeneration) {
                return;
            }
            send(operation, method, pathSegments, body, query, authenticationRequired, extraHeaders,
                 profile, retryAttempt + 1);
        };
    }
    trackJsonReply(reply, operation, authenticationRequired,
                   method != QNetworkAccessManager::GetOperation, retryAttempt, retryRequest);
}

void ApiClient::trackJsonReply(QNetworkReply *reply, const QString &operation,
                               bool authenticationRequired, bool mutation, int retryAttempt,
                               const std::function<void()> &retryRequest)
{
    reply->setReadBufferSize(m_maxResponseBytes + 1);
    ++m_activeRequests;
    const bool foreground = !reply->property("forgeBackground").toBool();
    if (foreground) { ++m_foregroundRequests; emit connectionActivityChanged(true); }
    const auto buffer = QSharedPointer<QByteArray>::create();
    const auto tooLarge = QSharedPointer<bool>::create(false);
    connect(reply, &QNetworkReply::readyRead, this, [this, reply, buffer, tooLarge]() {
        if (reply->isOpen()) {
            buffer->append(reply->read(qMax<qint64>(0, m_maxResponseBytes - buffer->size()) + 1));
        }
        if (buffer->size() > m_maxResponseBytes) {
            *tooLarge = true;
            reply->abort();
        }
    });
    connect(reply, &QNetworkReply::sslErrors, this, [this, reply](const QList<QSslError> &errors) {
        QStringList descriptions;
        descriptions.reserve(errors.size());
        for (const auto &error : errors) {
            descriptions.append(error.errorString());
        }
        reply->setProperty("forgeTlsRejected", true);
        reply->abort();
        emit tlsRejected(descriptions.join(QStringLiteral("; ")));
    });
    connect(reply, &QNetworkReply::finished, this,
            [this, reply, operation, buffer, tooLarge, authenticationRequired, mutation,
             retryAttempt, retryRequest, foreground]() {
                if (reply->isOpen()) {
                    buffer->append(reply->read(qMax<qint64>(0, m_maxResponseBytes - buffer->size()) + 1));
                }
                if (buffer->size() > m_maxResponseBytes) {
                    *tooLarge = true;
                }
                m_activeRequests = qMax(0, m_activeRequests - 1);
                if (foreground) {
                    m_foregroundRequests = qMax(0, m_foregroundRequests - 1);
                    emit connectionActivityChanged(m_foregroundRequests > 0);
                }
                const auto statusCode = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
                const auto redirect = reply->attribute(QNetworkRequest::RedirectionTargetAttribute);
                const bool timedOut = reply->property("forgeTimedOut").toBool();
                const bool tlsRejected = reply->property("forgeTlsRejected").toBool();
                const bool userCanceled = reply->property("forgeUserCanceled").toBool();
                const auto networkError = reply->error();
                const bool transient = timedOut || statusCode == 0 || statusCode == 408
                    || statusCode == 425 || statusCode == 429 || statusCode >= 500;
                if (!userCanceled && (networkError != QNetworkReply::OperationCanceledError || timedOut)
                    && !*tooLarge && !redirect.isValid() && !tlsRejected && transient && !mutation
                    && retryRequest && retryAttempt < m_safeGetRetries) {
                    const int shift = qMin(retryAttempt, 3);
                    const int delay = qMin(30'000, m_retryBaseDelayMs * (1 << shift));
                    reply->deleteLater();
                    QTimer::singleShot(delay, this, retryRequest);
                    return;
                }
                if (*tooLarge) {
                    fail(operation, statusCode,
                         QStringLiteral("Server response exceeded the configured editor limit."),
                         QStringLiteral("response"),
                         QStringLiteral("The bounded response buffer rejected additional bytes."));
                } else if (redirect.isValid()) {
                    fail(operation, statusCode,
                         QStringLiteral("Redirects are not followed by the Editor client."),
                         QStringLiteral("security"),
                         QStringLiteral("A redirect response was rejected before credentials could be forwarded."));
                } else if (reply->error() != QNetworkReply::NoError || statusCode >= 400) {
                    if (authenticationRequired && statusCode == 401 && operation != QStringLiteral("auth-logout")) {
                        clearSession();
                    }
                    QString category = QStringLiteral("network");
                    if (tlsRejected) {
                        category = QStringLiteral("tls");
                    } else if (timedOut) {
                        category = QStringLiteral("timeout");
                    } else if (networkError == QNetworkReply::HostNotFoundError) {
                        category = QStringLiteral("dns");
                    } else if (networkError == QNetworkReply::ConnectionRefusedError) {
                        category = QStringLiteral("connection-refused");
                    } else if (networkError == QNetworkReply::RemoteHostClosedError) {
                        category = QStringLiteral("connection-reset");
                    } else if (networkError == QNetworkReply::SslHandshakeFailedError) {
                        category = QStringLiteral("tls");
                    } else if (userCanceled || networkError == QNetworkReply::OperationCanceledError) {
                        category = QStringLiteral("canceled");
                    } else if (statusCode == 401) {
                        category = QStringLiteral("authentication");
                    } else if (statusCode == 403) {
                        category = QStringLiteral("authorization");
                    } else if (statusCode == 409) {
                        category = QStringLiteral("conflict");
                    } else if (statusCode == 422 || statusCode == 400) {
                        category = QStringLiteral("validation");
                    } else if (statusCode == 429) {
                        category = QStringLiteral("rate-limit");
                    } else if (statusCode >= 500) {
                        category = QStringLiteral("server");
                    }
                    QString failureMessage = responseDetail(*buffer, reply->errorString());
                    if (timedOut) {
                        const auto timeoutSeconds = reply->property("forgeTimeoutMs").toInt() / 1000;
                        failureMessage = QStringLiteral("The server did not respond within %1 seconds.")
                                             .arg(timeoutSeconds);
                    } else if (category == QStringLiteral("canceled")) {
                        failureMessage = userCanceled
                            ? QStringLiteral("The request was canceled by the user before completion.")
                            : QStringLiteral("The request was canceled before completion.");
                    }
                    const auto safeEndpoint = reply->request().url()
                                                  .adjusted(QUrl::RemoveUserInfo | QUrl::RemoveQuery
                                                            | QUrl::RemoveFragment)
                                                  .toString(QUrl::FullyEncoded);
                    auto requestId = QString::fromLatin1(reply->rawHeader("X-Request-ID")).trimmed();
                    static const QRegularExpression RequestIdPattern(
                        QStringLiteral(R"(^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$)"));
                    if (!RequestIdPattern.match(requestId).hasMatch()) {
                        requestId.clear();
                    }
                    auto details = QStringLiteral("Network error %1; HTTP status %2; attempt %3.\nEndpoint: %4")
                                       .arg(static_cast<int>(networkError))
                                       .arg(statusCode)
                                       .arg(retryAttempt + 1)
                                       .arg(safeEndpoint);
                    if (!requestId.isEmpty()) {
                        details += QStringLiteral("\nRequest ID: %1").arg(requestId);
                    }
                    fail(operation, statusCode, failureMessage, category, details,
                         mutation && (timedOut || statusCode == 0));
                } else if (statusCode == 204 && buffer->isEmpty()) {
                    emit jsonReceived(operation, QJsonObject{});
                } else {
                    QJsonParseError parseError;
                    const auto document = QJsonDocument::fromJson(*buffer, &parseError);
                    if (parseError.error != QJsonParseError::NoError || !document.isObject()) {
                        fail(operation, statusCode,
                             QStringLiteral("Server returned an invalid JSON object."),
                             QStringLiteral("response"), parseError.errorString());
                    } else {
                        const auto payload = document.object();
                        if (operation.startsWith(QStringLiteral("auth-"))
                            && operation != QStringLiteral("auth-logout")) {
                            QString tokenError;
                            if (!setSessionToken(payload.value(QStringLiteral("access_token")).toString().toUtf8(),
                                                 &tokenError)) {
                                fail(operation, statusCode, tokenError,
                                     QStringLiteral("authentication"));
                                reply->deleteLater();
                                return;
                            }
                        }
                        emit jsonReceived(operation, payload);
                    }
                }
                reply->deleteLater();
            });
}

void ApiClient::login(const QString &username, const QString &password)
{
    send(QStringLiteral("auth-login"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("auth"), QStringLiteral("login")},
         QJsonObject{{QStringLiteral("username"), username}, {QStringLiteral("password"), password}},
         {}, false, {}, RequestProfile::Authentication);
}

void ApiClient::registerMember(const QString &invitation, const QString &username, const QString &password,
                               const QString &displayName)
{
    const auto normalizedInvitation = invitation.trimmed();
    if (!validInvitationToken(normalizedInvitation)) {
        fail(QStringLiteral("auth-register"), 0,
             QStringLiteral("The invitation token format is invalid."),
             QStringLiteral("validation"));
        return;
    }
    send(QStringLiteral("auth-register"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("auth"), QStringLiteral("register")},
         QJsonObject{{QStringLiteral("invitation"), normalizedInvitation},
                     {QStringLiteral("username"), username},
                     {QStringLiteral("password"), password},
                     {QStringLiteral("display_name"), displayName}},
         {}, false, {}, RequestProfile::Authentication);
}

void ApiClient::setupFounder(const QByteArray &setupToken, const QString &username, const QString &password,
                             const QString &displayName)
{
    const auto normalizedToken = setupToken.trimmed();
    if (!validSetupToken(normalizedToken)) {
        fail(QStringLiteral("auth-setup"), 0,
             QStringLiteral("The founder setup token must be 32–512 printable ASCII characters; surrounding whitespace is ignored, but whitespace inside the token is not allowed."),
             QStringLiteral("validation"));
        return;
    }
    send(QStringLiteral("auth-setup"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("setup"), QStringLiteral("founder")},
         QJsonObject{{QStringLiteral("username"), username},
                     {QStringLiteral("password"), password},
                     {QStringLiteral("display_name"), displayName}},
         {}, false, {{QByteArray("X-Forge-Setup-Token"), normalizedToken}},
         RequestProfile::Authentication);
}

void ApiClient::fetchSetupStatus(const QString &operation)
{
    send(operation, QNetworkAccessManager::GetOperation,
         {QStringLiteral("setup"), QStringLiteral("status")}, {}, {}, false, {},
         RequestProfile::Authentication);
}

void ApiClient::logout()
{
    cancelActiveRequests();
    send(QStringLiteral("auth-logout"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("auth"), QStringLiteral("logout")});
    clearSession();
}

void ApiClient::fetchCapabilities(const QString &project)
{
    QUrlQuery query;
    if (!project.isEmpty()) { query.addQueryItem(QStringLiteral("project"), project); }
    send(project.isEmpty() ? QStringLiteral("capabilities") : QStringLiteral("capabilities:%1").arg(project),
         QNetworkAccessManager::GetOperation, {QStringLiteral("capabilities")}, {}, query);
}

void ApiClient::fetchProfile(const QString &project)
{
    QUrlQuery query;
    if (!project.isEmpty()) { query.addQueryItem(QStringLiteral("project"), project); }
    send(QStringLiteral("team-me"), QNetworkAccessManager::GetOperation, {QStringLiteral("me")}, {}, query);
}

void ApiClient::updateProfile(const QJsonObject &values, const QString &project)
{
    QUrlQuery query;
    if (!project.isEmpty()) { query.addQueryItem(QStringLiteral("project"), project); }
    send(QStringLiteral("team-profile-update"), QNetworkAccessManager::CustomOperation,
         {QStringLiteral("me")}, values, query);
}

void ApiClient::fetchProjects()
{
    send(QStringLiteral("projects"), QNetworkAccessManager::GetOperation, {QStringLiteral("projects")});
}

void ApiClient::createProject(const QString &directoryName, const QString &slug)
{
    send(QStringLiteral("create-project"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("projects")},
         QJsonObject{{QStringLiteral("name"), directoryName}, {QStringLiteral("slug"), slug}});
}

void ApiClient::fetchDocuments(const QString &project)
{
    send(QStringLiteral("documents:%1").arg(project), QNetworkAccessManager::GetOperation,
         {QStringLiteral("projects"), project, QStringLiteral("documents")});
}

void ApiClient::fetchDocument(const QString &project, const QString &documentPath,
                              const QString &operation)
{
    QString error;
    const auto documentSegments = documentPathSegments(documentPath, &error);
    const auto effectiveOperation = operation.isEmpty()
        ? QStringLiteral("document:%1:%2").arg(project, documentPath)
        : operation;
    if (documentSegments.isEmpty()) {
        fail(effectiveOperation, 0, error, QStringLiteral("validation"));
        return;
    }
    QStringList route{QStringLiteral("projects"), project, QStringLiteral("documents")};
    route.append(documentSegments);
    send(effectiveOperation, QNetworkAccessManager::GetOperation, route);
}

void ApiClient::saveDocument(const QString &project, const QString &documentPath, const QByteArray &content,
                             const QString &expectedSha256)
{
    const auto operation = QStringLiteral("save:%1:%2").arg(project, documentPath);
    QString error;
    const auto documentSegments = documentPathSegments(documentPath, &error);
    if (documentSegments.isEmpty()) {
        fail(operation, 0, error, QStringLiteral("validation"));
        return;
    }
    QStringList route{QStringLiteral("projects"), project, QStringLiteral("documents")};
    route.append(documentSegments);
    send(operation, QNetworkAccessManager::PutOperation, route,
         QJsonObject{{QStringLiteral("content"), QString::fromUtf8(content)},
                     {QStringLiteral("expected_sha256"), expectedSha256}});
}

void ApiClient::validateProject(const QString &project)
{
    send(QStringLiteral("validate:%1").arg(project), QNetworkAccessManager::PostOperation,
         {QStringLiteral("projects"), project, QStringLiteral("validate")});
}

void ApiClient::fetchMembers()
{
    send(QStringLiteral("team-members"), QNetworkAccessManager::GetOperation, {QStringLiteral("members")});
}

void ApiClient::fetchRoles()
{
    send(QStringLiteral("team-roles"), QNetworkAccessManager::GetOperation, {QStringLiteral("roles")});
}

void ApiClient::createRole(const QString &name, int rank, const QJsonArray &permissions,
                           const QJsonArray &documentAllow, const QJsonArray &documentDeny,
                           const QJsonArray &databaseAllow)
{
    send(QStringLiteral("team-role-create"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("roles")},
         QJsonObject{{QStringLiteral("name"), name},
                     {QStringLiteral("rank"), rank},
                     {QStringLiteral("permissions"), permissions},
                     {QStringLiteral("document_allow"), documentAllow},
                     {QStringLiteral("document_deny"), documentDeny},
                     {QStringLiteral("database_allow"), databaseAllow}});
}

void ApiClient::updateMember(const QString &userId, const QJsonArray &memberships, bool active)
{
    send(QStringLiteral("team-member-update"), QNetworkAccessManager::PutOperation,
         {QStringLiteral("members"), userId},
         QJsonObject{{QStringLiteral("memberships"), memberships},
                     {QStringLiteral("active"), active}});
}

void ApiClient::createInvitation(const QString &roleId, const QString &project, int expiresHours)
{
    const QJsonArray memberships{QJsonObject{{QStringLiteral("role_id"), roleId},
                                             {QStringLiteral("project"), project}}};
    send(QStringLiteral("team-invitation"), QNetworkAccessManager::PostOperation,
         {QStringLiteral("invitations")},
         QJsonObject{{QStringLiteral("memberships"), memberships},
                     {QStringLiteral("expires_hours"), expiresHours}});
}

void ApiClient::fetchAreas(const QString &project)
{
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("project"), project);
    send(QStringLiteral("team-areas:%1").arg(project), QNetworkAccessManager::GetOperation, {QStringLiteral("areas")}, {},
         query);
}

void ApiClient::createArea(const QString &project, const QString &name, const QString &description,
                           const QString &visibility, int minimumRank)
{
    send(QStringLiteral("team-area-create:%1").arg(project), QNetworkAccessManager::PostOperation,
         {QStringLiteral("areas")},
         QJsonObject{{QStringLiteral("project"), project},
                     {QStringLiteral("name"), name},
                     {QStringLiteral("description"), description},
                     {QStringLiteral("visibility"), visibility},
                     {QStringLiteral("minimum_rank"), minimumRank},
                     {QStringLiteral("allowed_role_ids"), QJsonArray{}}});
}

void ApiClient::fetchMessages(const QString &areaId)
{
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("limit"), QStringLiteral("200"));
    send(QStringLiteral("team-messages:%1").arg(areaId), QNetworkAccessManager::GetOperation,
         {QStringLiteral("areas"), areaId, QStringLiteral("messages")}, {}, query, true, {}, RequestProfile::Background);
}

void ApiClient::postMessage(const QString &areaId, const QString &body, bool announcement)
{
    send(QStringLiteral("team-message:%1").arg(areaId), QNetworkAccessManager::PostOperation,
         {QStringLiteral("areas"), areaId, QStringLiteral("messages")},
         QJsonObject{{QStringLiteral("body"), body},
                     {QStringLiteral("kind"), announcement ? QStringLiteral("announcement")
                                                           : QStringLiteral("message")}});
}

void ApiClient::fetchAttachments(const QString &areaId)
{
    send(QStringLiteral("team-attachments:%1").arg(areaId), QNetworkAccessManager::GetOperation,
         {QStringLiteral("areas"), areaId, QStringLiteral("attachments")}, {}, {}, true, {}, RequestProfile::Background);
}

void ApiClient::uploadAttachment(const QString &areaId, const QString &filePath, qsizetype maxBytes)
{
    if (!isConfigured()) {
        fail(QStringLiteral("team-attachment-upload:%1").arg(areaId), 0,
             QStringLiteral("Connect and sign in to a Forge server first."),
             QStringLiteral("authentication"));
        return;
    }
    const QFileInfo info(filePath);
    const auto name = info.fileName();
    if (!info.exists() || !info.isFile() || info.isSymLink() || name.isEmpty()
        || maxBytes < 1 || maxBytes > MaximumAttachmentSnapshot || info.size() > maxBytes
        || name.size() > 255 || name.contains(u'\r') || name.contains(u'\n') || name.contains(u'"')
        || name.contains(u';') || name.contains(QChar::Null)) {
        fail(QStringLiteral("team-attachment-upload:%1").arg(areaId), 0,
             QStringLiteral("The selected file is unsafe or exceeds the server attachment limit."),
             QStringLiteral("validation"));
        return;
    }
    // Read and verify an immutable file snapshot away from the GUI thread.
    const auto generation = m_requestGeneration;
    const auto canceled = QSharedPointer<std::atomic_bool>::create(false);
    m_fileJobs.append(canceled);
    emit fileTransferProgress(QStringLiteral("team-attachment-upload:%1").arg(areaId), 0, QStringLiteral("Preparing %1…").arg(name));
    m_filePool.start(QRunnable::create([this, areaId, name, path = info.absoluteFilePath(), maxBytes, generation, canceled] {
        QByteArray snapshot;
        QString error;
        const bool valid = !canceled->load() && safeAttachmentSnapshot(path, maxBytes, &snapshot, &error);
        QMetaObject::invokeMethod(this, [this, areaId, name, snapshot, error, valid, generation, canceled] {
            m_fileJobs.removeAll(canceled);
            if (generation != m_requestGeneration || canceled->load()) { return; }
            if (!valid) { fail(QStringLiteral("team-attachment-upload:%1").arg(areaId), 0, error, QStringLiteral("validation")); return; }
            postAttachment(areaId, name, snapshot);
        }, Qt::QueuedConnection);
    }));
}

void ApiClient::postAttachment(const QString &areaId, const QString &name, const QByteArray &snapshot)
{
    auto *multipart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart part;
    part.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/octet-stream"));
    part.setRawHeader("Content-Disposition", QByteArray("form-data; name=\"upload\"; filename=\"") + name.toUtf8() + QByteArray("\""));
    part.setBody(snapshot);
    multipart->append(part);

    QNetworkRequest request(endpointFor(
        {QStringLiteral("areas"), areaId, QStringLiteral("attachments")}));
    request.setRawHeader("Accept", "application/json");
    request.setRawHeader("Authorization", QByteArray("Bearer ") + m_sessionToken);
    hardenRequest(request);
    request.setTransferTimeout(m_uploadTimeoutMs);
    auto *reply = m_network.post(request, multipart);
    reply->setProperty("forgeFileTransfer", true);
    emit fileTransferProgress(QStringLiteral("team-attachment-upload:%1").arg(areaId), 0, QStringLiteral("Uploading %1…").arg(name));
    connect(reply, &QNetworkReply::uploadProgress, this, [this, areaId, name](qint64 sent, qint64 total) {
        const int percent = total > 0 ? static_cast<int>(qBound<qint64>(0LL, sent * 100 / total, 100LL)) : 0;
        emit fileTransferProgress(QStringLiteral("team-attachment-upload:%1").arg(areaId), percent,
            percent == 100 ? QStringLiteral("Server is saving %1…").arg(name) : QStringLiteral("Uploading %1 · %2%").arg(name).arg(percent));
    });
    multipart->setParent(reply);
    auto *deadline = new QTimer(reply);
    deadline->setSingleShot(true);
    connect(deadline, &QTimer::timeout, reply, [reply] {
        reply->setProperty("forgeTimedOut", true);
        reply->abort();
    });
    reply->setProperty("forgeTimeoutMs", m_uploadTimeoutMs);
    deadline->start(m_uploadTimeoutMs);
    trackJsonReply(reply, QStringLiteral("team-attachment-upload:%1").arg(areaId), true, true, 0);
}

void ApiClient::downloadAttachment(const QString &attachmentId, const QString &targetPath,
                                   qsizetype maxBytes, const QString &sha256)
{
    const auto operation = QStringLiteral("team-attachment-download");
    if (!isConfigured() || targetPath.isEmpty() || maxBytes < 1 || maxBytes > MaximumAttachmentSnapshot) {
        fail(operation, 0, QStringLiteral("A signed-in server and safe target path are required."),
             QStringLiteral("validation"));
        return;
    }
    QNetworkRequest request(endpointFor({QStringLiteral("attachments"), attachmentId}));
    request.setRawHeader("Accept", "application/octet-stream");
    request.setRawHeader("Authorization", QByteArray("Bearer ") + m_sessionToken);
    hardenRequest(request);
    request.setTransferTimeout(m_downloadTimeoutMs);
    auto *reply = m_network.get(request);
    reply->setProperty("forgeFileTransfer", true);
    connect(reply, &QNetworkReply::downloadProgress, this, [this, operation](qint64 received, qint64 total) {
        const int percent = total > 0 ? static_cast<int>(qBound<qint64>(0LL, received * 100 / total, 100LL)) : 0;
        emit fileTransferProgress(operation, percent, QStringLiteral("Downloading · %1 KiB").arg(received / 1024));
    });
    auto *deadline = new QTimer(reply);
    deadline->setSingleShot(true);
    connect(deadline, &QTimer::timeout, reply, [reply] {
        reply->setProperty("forgeTimedOut", true);
        reply->abort();
    });
    reply->setProperty("forgeTimeoutMs", m_downloadTimeoutMs);
    deadline->start(m_downloadTimeoutMs);
    reply->setReadBufferSize(maxBytes + 1);
    ++m_activeRequests;
    ++m_foregroundRequests;
    emit connectionActivityChanged(true);
    const auto buffer = QSharedPointer<QByteArray>::create();
    const auto tooLarge = QSharedPointer<bool>::create(false);
    connect(reply, &QNetworkReply::readyRead, this, [reply, buffer, tooLarge, maxBytes]() {
        if (reply->isOpen()) {
            buffer->append(reply->read(qMax<qint64>(0, maxBytes - buffer->size()) + 1));
        }
        if (buffer->size() > maxBytes) {
            *tooLarge = true;
            reply->abort();
        }
    });
    connect(reply, &QNetworkReply::sslErrors, this, [this, reply](const QList<QSslError> &errors) {
        QStringList descriptions;
        for (const auto &error : errors) {
            descriptions.append(error.errorString());
        }
        reply->setProperty("forgeTlsRejected", true);
        reply->abort();
        emit tlsRejected(descriptions.join(QStringLiteral("; ")));
    });
    connect(reply, &QNetworkReply::finished, this,
            [this, reply, operation, targetPath, buffer, tooLarge, maxBytes, sha256]() {
                if (reply->isOpen()) {
                    buffer->append(reply->read(qMax<qint64>(0, maxBytes - buffer->size()) + 1));
                }
                if (buffer->size() > maxBytes) {
                    *tooLarge = true;
                }
                m_activeRequests = qMax(0, m_activeRequests - 1);
                m_foregroundRequests = qMax(0, m_foregroundRequests - 1);
                emit connectionActivityChanged(m_foregroundRequests > 0);
                const auto statusCode = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
                const auto redirect = reply->attribute(QNetworkRequest::RedirectionTargetAttribute);
                const bool timedOut = reply->property("forgeTimedOut").toBool();
                const bool tlsRejected = reply->property("forgeTlsRejected").toBool();
                const bool userCanceled = reply->property("forgeUserCanceled").toBool();
                if (*tooLarge) {
                    fail(operation, statusCode,
                         QStringLiteral("Attachment exceeded the server-advertised size limit."),
                         QStringLiteral("response"));
                } else if (redirect.isValid()) {
                    fail(operation, statusCode,
                         QStringLiteral("Redirects are not followed by the Editor client."),
                         QStringLiteral("security"));
                } else if (reply->error() != QNetworkReply::NoError || statusCode >= 400) {
                    if (statusCode == 401) {
                        clearSession();
                    }
                    const auto category = userCanceled ? QStringLiteral("canceled") : tlsRejected ? QStringLiteral("tls")
                        : timedOut ? QStringLiteral("timeout")
                        : statusCode == 401 ? QStringLiteral("authentication")
                        : statusCode == 403 ? QStringLiteral("authorization")
                        : statusCode >= 500 ? QStringLiteral("server")
                        : QStringLiteral("network");
                    fail(operation, statusCode,
                         timedOut ? QStringLiteral("The download exceeded its configured timeout.")
                                  : responseDetail(*buffer, reply->errorString()),
                         category,
                         QStringLiteral("Network error %1; HTTP status %2.")
                             .arg(static_cast<int>(reply->error())).arg(statusCode));
                } else {
                    const auto generation = m_requestGeneration;
                    const auto canceled = QSharedPointer<std::atomic_bool>::create(false);
                    m_fileJobs.append(canceled);
                    emit fileTransferProgress(operation, 100, QStringLiteral("Verifying and saving file…"));
                    m_filePool.start(QRunnable::create([this, operation, targetPath, buffer, sha256, generation, canceled] {
                        const bool integrity = sha256.isEmpty() || QString::fromLatin1(QCryptographicHash::hash(*buffer, QCryptographicHash::Sha256).toHex()) == sha256.toLower();
                        QSaveFile output(targetPath);
                        bool saved = integrity && !canceled->load() && output.open(QIODevice::WriteOnly);
                        for (qsizetype offset = 0; saved && offset < buffer->size(); offset += 1024 * 1024) {
                            const auto length = qMin<qsizetype>(1024 * 1024, buffer->size() - offset);
                            saved = !canceled->load() && output.write(buffer->constData() + offset, length) == length;
                        }
                        saved = saved && !canceled->load() && output.commit();
                        if (!saved) { output.cancelWriting(); }
                        QMetaObject::invokeMethod(this, [this, operation, targetPath, saved, integrity, generation, canceled] {
                            m_fileJobs.removeAll(canceled);
                            if (generation != m_requestGeneration || canceled->load()) { return; }
                            if (saved) { emit fileDownloaded(operation, targetPath); }
                            else { fail(operation, 0, integrity ? QStringLiteral("The file could not be saved. Choose a writable folder and try again.")
                                                               : QStringLiteral("File integrity verification failed. The destination was preserved."), QStringLiteral("filesystem")); }
                        }, Qt::QueuedConnection);
                    }));
                }
                reply->deleteLater();
            });
}

void ApiClient::fetchNotes(const QString &project)
{
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("project"), project);
    send(QStringLiteral("team-notes:%1").arg(project), QNetworkAccessManager::GetOperation, {QStringLiteral("notes")}, {},
         query);
}

void ApiClient::createNote(const QString &project, const QString &areaId, const QString &title,
                           const QString &body, const QString &visibility, int minimumRank)
{
    send(QStringLiteral("team-note-create:%1").arg(project), QNetworkAccessManager::PostOperation,
         {QStringLiteral("notes")},
         QJsonObject{{QStringLiteral("project"), project},
                     {QStringLiteral("area_id"), areaId.isEmpty() ? QJsonValue(QJsonValue::Null)
                                                                  : QJsonValue(areaId)},
                     {QStringLiteral("title"), title},
                     {QStringLiteral("body"), body},
                     {QStringLiteral("visibility"), visibility},
                     {QStringLiteral("minimum_rank"), minimumRank},
                     {QStringLiteral("allowed_role_ids"), QJsonArray{}}});
}

void ApiClient::fetchDatabases(const QString &project)
{
    send(QStringLiteral("team-databases:%1").arg(project), QNetworkAccessManager::GetOperation,
         {QStringLiteral("projects"), project, QStringLiteral("databases")});
}

void ApiClient::fetchDatabaseRows(const QString &project, const QString &alias, const QString &table,
                                  int limit, int offset)
{
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("limit"), QString::number(qBound(1, limit, 500)));
    query.addQueryItem(QStringLiteral("offset"), QString::number(qMax(0, offset)));
    send(QStringLiteral("team-rows:%1:%2:%3").arg(project, alias, table),
         QNetworkAccessManager::GetOperation,
         {QStringLiteral("projects"), project, QStringLiteral("databases"), alias,
          QStringLiteral("tables"), table, QStringLiteral("rows")},
         {}, query);
}

void ApiClient::startCall(const QString &areaId, const QString &mode)
{
    send(QStringLiteral("team-call:%1").arg(areaId), QNetworkAccessManager::PostOperation,
         {QStringLiteral("calls")},
         QJsonObject{{QStringLiteral("area_id"), areaId}, {QStringLiteral("mode"), mode}});
}

void ApiClient::createCallTicket(const QString &callId)
{
    send(QStringLiteral("team-call-ticket:%1").arg(callId), QNetworkAccessManager::PostOperation,
         {QStringLiteral("calls"), callId, QStringLiteral("ticket")});
}

void ApiClient::fetchCalls(const QString &areaId)
{
    send(QStringLiteral("team-calls:%1").arg(areaId), QNetworkAccessManager::GetOperation,
         {QStringLiteral("areas"), areaId, QStringLiteral("calls")}, {}, {}, true, {}, RequestProfile::Background);
}

void ApiClient::fetchAudit(const QString &project)
{
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("limit"), QStringLiteral("200"));
    if (!project.isEmpty()) {
        query.addQueryItem(QStringLiteral("project"), project);
    }
    send(QStringLiteral("team-audit:%1").arg(project), QNetworkAccessManager::GetOperation, {QStringLiteral("audit")}, {},
         query);
}

QUrl ApiClient::callClientUrl(const QString &path, const QString &ticket) const
{
    static const QRegularExpression ticketPattern(
        QStringLiteral(R"(\Ajfc_[A-Za-z0-9_-]{40,80}\z)"));
    static const QRegularExpression pathPattern(
        QStringLiteral(R"(\A/__forge/editor/v1/call-client/[A-Za-z0-9][A-Za-z0-9_-]{0,127}\z)"));
    if (!hasServer() || !pathPattern.match(path).hasMatch()
        || !ticketPattern.match(ticket).hasMatch()) {
        return {};
    }
    auto relative = path;
    while (relative.startsWith(u'/')) {
        relative.remove(0, 1);
    }
    auto basePath = m_serverUrl.path();
    if (!basePath.endsWith(u'/')) {
        basePath += u'/';
    }
    QUrl result(m_serverUrl);
    result.setPath(basePath + relative);
    QUrlQuery fragment;
    fragment.addQueryItem(QStringLiteral("ticket"), ticket);
    result.setFragment(fragment.query(QUrl::FullyEncoded));
    return result;
}
