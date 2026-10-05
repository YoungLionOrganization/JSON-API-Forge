#pragma once
#include <QJsonArray>
#include <QJsonObject>
#include <QPlainTextEdit>
#include <QTextBrowser>

class ChatView final : public QTextBrowser {
    Q_OBJECT
public:
    explicit ChatView(QWidget *parent = nullptr);
    bool setConversation(const QJsonArray &messages, const QJsonArray &attachments);
    void clearConversation();
    qsizetype messageCount() const { return m_messages.size(); }
    QJsonObject messageAt(qsizetype index) const { return m_messages.at(index).toObject(); }
signals:
    void attachmentRequested(const QString &id);
    void fileDropped(const QString &path);
protected:
    void dragEnterEvent(QDragEnterEvent *event) override;
    void dragMoveEvent(QDragMoveEvent *event) override;
    void dropEvent(QDropEvent *event) override;
private:
    bool m_conversationLoaded = false;
    QJsonArray m_messages;
    QJsonArray m_attachments;
};

class ChatComposer final : public QPlainTextEdit {
    Q_OBJECT
public:
    explicit ChatComposer(QWidget *parent = nullptr);
signals:
    void sendRequested();
    void fileDropped(const QString &path);
protected:
    void keyPressEvent(QKeyEvent *event) override;
    void insertFromMimeData(const QMimeData *source) override;
    void resizeEvent(QResizeEvent *event) override;
private:
    void fitContent();
};
