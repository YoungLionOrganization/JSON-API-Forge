#include "ChatView.hpp"
#include <QDateTime>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QKeyEvent>
#include <QLocale>
#include <QMimeData>
#include <QResizeEvent>
#include <QScrollBar>
#include <QTextBlock>
#include <QTextLayout>
#include <QTimer>
#include <QUrl>
#include <QtMath>

namespace {
QString escaped(const QString &value) { return value.toHtmlEscaped().replace(u'\n', QStringLiteral("<br>")); }
QString timeText(const QString &value)
{
    const auto date = QDateTime::fromString(value, Qt::ISODateWithMs);
    return date.isValid() ? QLocale().toString(date.toLocalTime(), QLocale::ShortFormat) : value;
}
QString localFile(const QMimeData *mime)
{
    const auto urls = mime->urls();
    return urls.size() == 1 && urls.first().isLocalFile() ? urls.first().toLocalFile() : QString();
}
}

ChatView::ChatView(QWidget *parent) : QTextBrowser(parent)
{
    setObjectName(QStringLiteral("messageList"));
    setOpenLinks(false);
    setOpenExternalLinks(false);
    setAcceptDrops(true);
    setMinimumHeight(0);
    setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Ignored);
    setFrameShape(QFrame::NoFrame);
    document()->setDocumentMargin(12);
    connect(this, &QTextBrowser::anchorClicked, this, [this](const QUrl &url) {
        if (url.scheme() == QStringLiteral("forge-file")) { emit attachmentRequested(url.path()); }
    });
    clearConversation();
}

void ChatView::clearConversation()
{
    m_conversationLoaded = false;
    m_messages = {};
    m_attachments = {};
    setHtml(QStringLiteral("<p style='color:#a6a8ad'>Choose a space to see its conversation.</p>"));
}

bool ChatView::setConversation(const QJsonArray &messages, const QJsonArray &attachments)
{
    if (m_conversationLoaded && m_messages == messages && m_attachments == attachments) { return false; }
    m_conversationLoaded = true;
    auto *scroll = verticalScrollBar();
    const bool bottom = scroll->value() >= scroll->maximum() - 12;
    const int position = scroll->value();
    const auto cursor = textCursor();
    const int anchor = cursor.anchor();
    const int selection = cursor.position();
    m_messages = messages;
    m_attachments = attachments;
    QString content = QStringLiteral("<style>body{color:#e4e5e8;font-family:'Segoe UI',sans-serif;}p{margin:5px 0;}a{color:#f2b84b;text-decoration:none;}</style>");
    if (messages.isEmpty()) { content += QStringLiteral("<p style='color:#a6a8ad'>This is the beginning of your conversation. Send the first message.</p>"); }
    QString previousDay;
    for (const auto &value : messages) {
        const auto message = value.toObject();
        const auto date = QDateTime::fromString(message.value(QStringLiteral("created_at")).toString(), Qt::ISODateWithMs);
        const auto day = date.isValid() ? QLocale().toString(date.toLocalTime().date(), QLocale::LongFormat) : QString();
        if (!day.isEmpty() && day != previousDay) {
            content += QStringLiteral("<p align='center' style='color:#969aa4;font-size:11px;'>— %1 —</p>").arg(escaped(day));
            previousDay = day;
        }
        const auto author = message.value(QStringLiteral("display_name")).toString(QStringLiteral("Team member"));
        content += QStringLiteral("<table width='100%' cellspacing='0' cellpadding='6'><tr><td width='38' valign='top' bgcolor='#38313f'><b style='color:#f2b84b;font-size:18px'>%1</b></td><td valign='top'><b style='color:#f2b84b'>%2</b> <span style='color:#969aa4;font-size:10px'>%3</span>%4<p>%5</p></td></tr></table><p style='font-size:3px'>&nbsp;</p>")
            .arg(escaped(author.left(1).toUpper()), escaped(author), escaped(timeText(message.value(QStringLiteral("created_at")).toString())),
                 message.value(QStringLiteral("kind")).toString() == QStringLiteral("announcement") ? QStringLiteral(" <b>· Announcement</b>") : QString(),
                 escaped(message.value(QStringLiteral("body")).toString()));
    }
    if (!attachments.isEmpty()) { content += QStringLiteral("<p style='color:#969aa4'><b>SHARED FILES</b></p>"); }
    for (const auto &value : attachments) {
        const auto file = value.toObject();
        const auto id = file.value(QStringLiteral("id")).toString();
        const auto url = QStringLiteral("forge-file:") + QString::fromLatin1(QUrl::toPercentEncoding(id));
        content += QStringLiteral("<table width='100%' cellpadding='8' bgcolor='#30333a'><tr><td><a href='%1'><b>↓ %2</b></a><br><span style='color:#a6a8ad;font-size:11px'>%3 · %4 KiB</span></td></tr></table><p style='font-size:3px'>&nbsp;</p>")
            .arg(url.toHtmlEscaped(), escaped(file.value(QStringLiteral("original_name")).toString()),
                 escaped(file.value(QStringLiteral("display_name")).toString()),
                 QString::number(file.value(QStringLiteral("size")).toDouble() / 1024.0, 'f', 1));
    }
    setHtml(content);
    if (!bottom && anchor != selection) {
        QTextCursor restored(document());
        const int maximum = qMax(0, document()->characterCount() - 1);
        restored.setPosition(qBound(0, anchor, maximum));
        restored.setPosition(qBound(0, selection, maximum), QTextCursor::KeepAnchor);
        setTextCursor(restored);
    }
    QTimer::singleShot(0, this, [this, bottom, position] {
        if (bottom) { verticalScrollBar()->setValue(verticalScrollBar()->maximum()); }
        else { verticalScrollBar()->setValue(position); }
    });
    return true;
}
void ChatView::dragEnterEvent(QDragEnterEvent *event)
{
    if (!localFile(event->mimeData()).isEmpty()) { event->acceptProposedAction(); }
    else { event->ignore(); }
}
void ChatView::dragMoveEvent(QDragMoveEvent *event)
{
    if (!localFile(event->mimeData()).isEmpty()) { event->acceptProposedAction(); }
    else { event->ignore(); }
}
void ChatView::dropEvent(QDropEvent *event)
{
    const auto path = localFile(event->mimeData());
    if (!path.isEmpty()) { emit fileDropped(path); event->acceptProposedAction(); }
    else { event->ignore(); }
}

ChatComposer::ChatComposer(QWidget *parent) : QPlainTextEdit(parent)
{
    setObjectName(QStringLiteral("messageComposer"));
    setMinimumHeight(46);
    setMaximumHeight(132);
    setFixedHeight(46);
    setPlaceholderText(QStringLiteral("Write a message…"));
    setAccessibleName(QStringLiteral("Message"));
    setToolTip(QStringLiteral("Enter to send · Shift+Enter for a new line"));
    auto colors = palette();
    colors.setColor(QPalette::PlaceholderText, QColor(QStringLiteral("#a6adbb")));
    setPalette(colors);
    connect(this, &QPlainTextEdit::textChanged, this, [this] {
        fitContent();
        if (toPlainText().size() <= 8000) { return; }
        const auto text = toPlainText();
        auto cursor = textCursor();
        // Do not split a pasted emoji's UTF-16 surrogate pair at the limit.
        cursor.setPosition(text.at(7999).isHighSurrogate() ? 7999 : 8000);
        cursor.movePosition(QTextCursor::End, QTextCursor::KeepAnchor);
        cursor.removeSelectedText();
    });
}
void ChatComposer::fitContent()
{
    qreal height = 22;
    for (auto block = document()->begin(); block.isValid() && height < 132; block = block.next()) {
        height += blockBoundingRect(block).height();
    }
    setFixedHeight(qBound(46, qCeil(height), 132));
}
void ChatComposer::resizeEvent(QResizeEvent *event)
{
    QPlainTextEdit::resizeEvent(event);
    if (event->size().width() != event->oldSize().width()) {
        QTimer::singleShot(0, this, &ChatComposer::fitContent);
    }
}
void ChatComposer::keyPressEvent(QKeyEvent *event)
{
    if ((event->key() == Qt::Key_Return || event->key() == Qt::Key_Enter) && !(event->modifiers() & Qt::ShiftModifier)) {
        if (!toPlainText().trimmed().isEmpty()) { emit sendRequested(); }
        event->accept(); return;
    }
    QPlainTextEdit::keyPressEvent(event);
}
void ChatComposer::insertFromMimeData(const QMimeData *source)
{
    const auto path = localFile(source);
    if (!path.isEmpty()) { emit fileDropped(path); }
    else { QPlainTextEdit::insertFromMimeData(source); }
}
