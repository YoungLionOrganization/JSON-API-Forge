#pragma once

#include <QJsonObject>
#include <QJsonValue>
#include <QStringList>
#include <QList>
#include <QWidget>

class QListWidget;
class QTableWidget;
class QTreeWidget;
class QLineEdit;
class QLabel;
class QPushButton;
class QSplitter;
class QResizeEvent;

class VisualDesigner final : public QWidget {
    Q_OBJECT

public:
    explicit VisualDesigner(QWidget *parent = nullptr);
    void setDocument(const QJsonObject &document);
    [[nodiscard]] QJsonObject document() const;
    void addPaletteComponent(const QString &label, const QString &collection, const QJsonObject &value);

public slots:
    void undo();
    void redo();
    void duplicateSelection();
    void removeSelection();
    void moveSelectionUp();
    void moveSelectionDown();

signals:
    void documentChanged(const QJsonObject &document);
    void statusMessage(const QString &message);

private:
    void installBuiltInComponents();
    void refreshCanvas();
    void showProperties();
    void applyPropertyEdit(int row, int column);
    void insertTemplate(const QByteArray &payload);
    [[nodiscard]] QStringList selectedPath() const;
    [[nodiscard]] QJsonValue selectedValue() const;
    void commit(const QJsonObject &document, const QStringList &selection);
    void replaceSelection(const QJsonValue &value);
    void updateControls();
    void filterPalette();
    void filterCanvas();
    void addProperty();
    void removeProperty();
    void editStructuredProperty(int row);
    void moveSelection(int direction);
    void resizeEvent(QResizeEvent *event) override;

    QListWidget *m_palette = nullptr;
    QTreeWidget *m_canvas = nullptr;
    QTableWidget *m_properties = nullptr;
    QLineEdit *m_paletteSearch = nullptr;
    QLineEdit *m_canvasSearch = nullptr;
    QLineEdit *m_propertySearch = nullptr;
    QLabel *m_summary = nullptr;
    QLabel *m_selectionLabel = nullptr;
    QLabel *m_componentDescription = nullptr;
    QLabel *m_feedback = nullptr;
    QPushButton *m_addComponent = nullptr;
    QPushButton *m_undo = nullptr;
    QPushButton *m_redo = nullptr;
    QPushButton *m_duplicate = nullptr;
    QPushButton *m_remove = nullptr;
    QPushButton *m_moveUp = nullptr;
    QPushButton *m_moveDown = nullptr;
    QPushButton *m_addProperty = nullptr;
    QPushButton *m_removeProperty = nullptr;
    QSplitter *m_splitter = nullptr;
    QList<QJsonObject> m_history;
    QList<QStringList> m_selections;
    qsizetype m_historyIndex = 0;
    QStringList m_selection;
    QJsonObject m_document;
    bool m_refreshing = false;
};
