#include "VisualDesigner.hpp"
#include "UiSizing.hpp"

#include <QAbstractItemView>
#include <QComboBox>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDragEnterEvent>
#include <QDropEvent>
#include <QFormLayout>
#include <QHeaderView>
#include <QHBoxLayout>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QMimeData>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QResizeEvent>
#include <QScrollArea>
#include <QSet>
#include <QShortcut>
#include <QSplitter>
#include <QStyledItemDelegate>
#include <QTableWidget>
#include <QTreeWidget>
#include <QVBoxLayout>

#include <functional>

namespace {
const auto ComponentMime = QStringLiteral("application/x-json-api-forge-component");
constexpr auto PathRole = Qt::UserRole + 1;
constexpr auto ValueRole = Qt::UserRole + 2;
constexpr auto ScalarRole = Qt::UserRole + 3;

class PaletteList final : public QListWidget {
public:
    using QListWidget::QListWidget;
protected:
    QStringList mimeTypes() const override { return {ComponentMime}; }
    QMimeData *mimeData(const QList<QListWidgetItem *> &items) const override
    {
        auto *mime = new QMimeData;
        if (!items.isEmpty()) { mime->setData(ComponentMime, items.first()->data(Qt::UserRole).toByteArray()); }
        return mime;
    }
};

class CanvasTree final : public QTreeWidget {
public:
    using QTreeWidget::QTreeWidget;
    std::function<void(const QByteArray &)> componentDropped;
protected:
    void dragEnterEvent(QDragEnterEvent *event) override
    {
        if (event->mimeData()->hasFormat(ComponentMime)) { event->acceptProposedAction(); }
        else { QTreeWidget::dragEnterEvent(event); }
    }
    void dragMoveEvent(QDragMoveEvent *event) override
    {
        if (event->mimeData()->hasFormat(ComponentMime)) { event->acceptProposedAction(); }
        else { QTreeWidget::dragMoveEvent(event); }
    }
    void dropEvent(QDropEvent *event) override
    {
        if (event->mimeData()->hasFormat(ComponentMime) && componentDropped) {
            componentDropped(event->mimeData()->data(ComponentMime));
            event->acceptProposedAction();
        } else { QTreeWidget::dropEvent(event); }
    }
};

QString typeName(const QJsonValue &value)
{
    switch (value.type()) {
    case QJsonValue::Bool: return QStringLiteral("boolean");
    case QJsonValue::Double: return QStringLiteral("number");
    case QJsonValue::String: return QStringLiteral("text");
    case QJsonValue::Array: return QStringLiteral("array");
    case QJsonValue::Object: return QStringLiteral("object");
    default: return QStringLiteral("null");
    }
}

QString valueText(const QJsonValue &value)
{
    if (value.isString()) { return value.toString(); }
    if (value.isBool()) { return value.toBool() ? QStringLiteral("true") : QStringLiteral("false"); }
    if (value.isDouble()) { return QString::number(value.toDouble(), 'g', 17); }
    if (value.isObject()) { return QString::fromUtf8(QJsonDocument(value.toObject()).toJson(QJsonDocument::Compact)); }
    if (value.isArray()) { return QString::fromUtf8(QJsonDocument(value.toArray()).toJson(QJsonDocument::Compact)); }
    return QStringLiteral("null");
}

QString summary(const QJsonValue &value)
{
    if (value.isObject()) { return QStringLiteral("%1 properties").arg(value.toObject().size()); }
    if (value.isArray()) { return QStringLiteral("%1 items").arg(value.toArray().size()); }
    return valueText(value).left(160);
}

bool parseValue(const QString &text, const QJsonValue &original, QJsonValue *value)
{
    if (original.isString()) { *value = text; return true; }
    if (text.size() > 8 * 1024 * 1024) { return false; }
    QJsonParseError error;
    const auto parsed = QJsonDocument::fromJson(QByteArray("{\"value\":") + text.trimmed().toUtf8() + QByteArray("}"), &error);
    if (error.error != QJsonParseError::NoError || !parsed.isObject()) { return false; }
    const auto candidate = parsed.object().value(QStringLiteral("value"));
    if (candidate.type() != original.type()) { return false; }
    *value = candidate;
    return true;
}

QJsonValue atPath(QJsonValue value, const QStringList &path)
{
    for (const auto &part : path) {
        if (value.isObject()) { value = value.toObject().value(part); }
        else if (value.isArray()) {
            bool ok = false;
            const auto index = part.toInt(&ok);
            const auto array = value.toArray();
            if (!ok || index < 0 || index >= array.size()) { return QJsonValue(QJsonValue::Undefined); }
            value = array.at(index);
        } else { return QJsonValue(QJsonValue::Undefined); }
    }
    return value;
}

QJsonValue changedAt(const QJsonValue &root, const QStringList &path, const QJsonValue &value, qsizetype depth = 0)
{
    if (depth == path.size()) { return value; }
    const auto &key = path.at(depth);
    if (root.isObject()) {
        auto object = root.toObject();
        const auto replacement = changedAt(object.value(key), path, value, depth + 1);
        if (replacement.isUndefined()) { object.remove(key); }
        else { object.insert(key, replacement); }
        return object;
    }
    if (root.isArray()) {
        auto array = root.toArray();
        bool ok = false;
        const auto index = key.toInt(&ok);
        if (!ok || index < 0 || index >= array.size()) { return root; }
        const auto replacement = changedAt(array.at(index), path, value, depth + 1);
        if (replacement.isUndefined()) { array.removeAt(index); }
        else { array.replace(index, replacement); }
        return array;
    }
    return root;
}

QJsonObject uniqueComponent(QJsonObject value, const QJsonArray &array)
{
    for (const auto &key : {QStringLiteral("name"), QStringLiteral("path")}) {
        if (!value.value(key).isString()) { continue; }
        QSet<QString> existing;
        for (const auto &entry : array) { existing.insert(entry.toObject().value(key).toString()); }
        const auto base = value.value(key).toString();
        auto name = base;
        int suffix = 2;
        while (existing.contains(name)) { name = base + QStringLiteral("-%1").arg(suffix++); }
        value.insert(key, name);
    }
    return value;
}

class PropertyDelegate final : public QStyledItemDelegate {
public:
    using QStyledItemDelegate::QStyledItemDelegate;
    QWidget *createEditor(QWidget *parent, const QStyleOptionViewItem &option, const QModelIndex &index) const override
    {
        if (index.data(ValueRole).value<QJsonValue>().isBool()) {
            auto *combo = new QComboBox(parent);
            combo->addItems({QStringLiteral("false"), QStringLiteral("true")});
            return combo;
        }
        return QStyledItemDelegate::createEditor(parent, option, index);
    }
    void setEditorData(QWidget *editor, const QModelIndex &index) const override
    {
        if (auto *combo = qobject_cast<QComboBox *>(editor)) { combo->setCurrentText(index.data(Qt::EditRole).toString()); }
        else { QStyledItemDelegate::setEditorData(editor, index); }
    }
    void setModelData(QWidget *editor, QAbstractItemModel *model, const QModelIndex &index) const override
    {
        if (auto *combo = qobject_cast<QComboBox *>(editor)) { model->setData(index, combo->currentText(), Qt::EditRole); }
        else { QStyledItemDelegate::setModelData(editor, model, index); }
    }
};
} // namespace

VisualDesigner::VisualDesigner(QWidget *parent) : QWidget(parent)
{
    setObjectName(QStringLiteral("visualDesigner"));
    auto *layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    m_summary = new QLabel(QStringLiteral("Build your API visually"), this);
    m_summary->setObjectName(QStringLiteral("visualSummary"));
    layout->addWidget(m_summary);
    m_splitter = new QSplitter(this);
    m_splitter->setObjectName(QStringLiteral("visualSplitter"));
    m_splitter->setChildrenCollapsible(false);
    m_splitter->setHandleWidth(4);
    const auto panel = [this](const QString &title, int minimum) {
        auto *widget = new QWidget(m_splitter);
        widget->setObjectName(QStringLiteral("visualPanel"));
        widget->setMinimumWidth(minimum);
        auto *box = new QVBoxLayout(widget);
        box->setContentsMargins(10, 12, 10, 10);
        box->setSpacing(8);
        auto *heading = new QLabel(title, widget);
        heading->setObjectName(QStringLiteral("panelEyebrow"));
        box->addWidget(heading);
        return widget;
    };
    auto *palettePanel = panel(QStringLiteral("COMPONENT LIBRARY"), 145);
    auto *paletteLayout = qobject_cast<QVBoxLayout *>(palettePanel->layout());
    m_paletteSearch = new QLineEdit(palettePanel);
    m_paletteSearch->setObjectName(QStringLiteral("componentSearch"));
    m_paletteSearch->setPlaceholderText(QStringLiteral("Find a component…"));
    m_paletteSearch->setClearButtonEnabled(true);
    paletteLayout->addWidget(m_paletteSearch);
    m_palette = new PaletteList(palettePanel);
    m_palette->setObjectName(QStringLiteral("componentPalette"));
    m_palette->setDragEnabled(true);
    m_palette->setDragDropMode(QAbstractItemView::DragOnly);
    m_palette->setSpacing(5);
    m_palette->setWordWrap(true);
    m_palette->setHorizontalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    paletteLayout->addWidget(m_palette, 1);
    m_componentDescription = new QLabel(QStringLiteral("Choose a component, then add it or drag it into the structure."), palettePanel);
    m_componentDescription->setWordWrap(true);
    m_componentDescription->setTextFormat(Qt::PlainText);
    paletteLayout->addWidget(m_componentDescription);
    m_addComponent = new QPushButton(QStringLiteral("Add component"), palettePanel);
    m_addComponent->setObjectName(QStringLiteral("addComponentButton"));
    paletteLayout->addWidget(m_addComponent);

    auto *canvasPanel = panel(QStringLiteral("DOCUMENT STRUCTURE"), 180);
    auto *canvasLayout = qobject_cast<QVBoxLayout *>(canvasPanel->layout());
    auto *toolbar = new QWidget(canvasPanel);
    toolbar->setObjectName(QStringLiteral("visualToolbar"));
    auto *actions = new QHBoxLayout(toolbar);
    actions->setContentsMargins(0, 0, 0, 0);
    actions->setSpacing(4);
    const auto action = [toolbar, actions](const QString &text, const QString &name) {
        auto *button = new QPushButton(text, toolbar);
        button->setObjectName(name);
        actions->addWidget(button);
        return button;
    };
    auto *root = action(QStringLiteral("Document"), QStringLiteral("visualRoot"));
    m_undo = action(QStringLiteral("Undo"), QStringLiteral("visualUndo"));
    m_redo = action(QStringLiteral("Redo"), QStringLiteral("visualRedo"));
    m_duplicate = action(QStringLiteral("Duplicate"), QStringLiteral("visualDuplicate"));
    m_remove = action(QStringLiteral("Delete"), QStringLiteral("visualDelete"));
    m_moveUp = action(QStringLiteral("↑"), QStringLiteral("visualMoveUp"));
    m_moveDown = action(QStringLiteral("↓"), QStringLiteral("visualMoveDown"));
    m_moveUp->setToolTip(QStringLiteral("Move array item up"));
    m_moveDown->setToolTip(QStringLiteral("Move array item down"));
    actions->addStretch();
    auto *toolbarScroll = new QScrollArea(canvasPanel);
    toolbarScroll->setWidget(toolbar);
    toolbarScroll->setWidgetResizable(true);
    toolbarScroll->setFrameShape(QFrame::NoFrame);
    toolbarScroll->setVerticalScrollBarPolicy(Qt::ScrollBarAlwaysOff);
    toolbarScroll->setFixedHeight(58);
    canvasLayout->addWidget(toolbarScroll);
    m_canvasSearch = new QLineEdit(canvasPanel);
    m_canvasSearch->setObjectName(QStringLiteral("structureSearch"));
    m_canvasSearch->setPlaceholderText(QStringLiteral("Search names, values and nested fields…"));
    m_canvasSearch->setClearButtonEnabled(true);
    canvasLayout->addWidget(m_canvasSearch);
    m_canvas = new CanvasTree(canvasPanel);
    m_canvas->setObjectName(QStringLiteral("designerCanvas"));
    m_canvas->setHeaderLabels({QStringLiteral("Name"), QStringLiteral("Type"), QStringLiteral("Details")});
    m_canvas->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    m_canvas->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    m_canvas->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    m_canvas->setAcceptDrops(true);
    m_canvas->setDragDropMode(QAbstractItemView::DropOnly);
    m_canvas->setAlternatingRowColors(true);
    static_cast<CanvasTree *>(m_canvas)->componentDropped = [this](const QByteArray &payload) { insertTemplate(payload); };
    canvasLayout->addWidget(m_canvas, 1);
    m_feedback = new QLabel(QStringLiteral("Select any field to inspect it. All edits preserve JSON types."), canvasPanel);
    m_feedback->setObjectName(QStringLiteral("visualFeedback"));
    m_feedback->setTextFormat(Qt::PlainText);
    m_feedback->setWordWrap(true);
    canvasLayout->addWidget(m_feedback);

    auto *propertyPanel = panel(QStringLiteral("PROPERTIES"), 195);
    auto *propertyLayout = qobject_cast<QVBoxLayout *>(propertyPanel->layout());
    m_selectionLabel = new QLabel(propertyPanel);
    m_selectionLabel->setObjectName(QStringLiteral("visualSelection"));
    m_selectionLabel->setTextFormat(Qt::PlainText);
    m_selectionLabel->setWordWrap(true);
    propertyLayout->addWidget(m_selectionLabel);
    m_propertySearch = new QLineEdit(propertyPanel);
    m_propertySearch->setPlaceholderText(QStringLiteral("Filter properties…"));
    m_propertySearch->setClearButtonEnabled(true);
    propertyLayout->addWidget(m_propertySearch);
    m_properties = new QTableWidget(propertyPanel);
    m_properties->setObjectName(QStringLiteral("propertyTable"));
    m_properties->setColumnCount(3);
    m_properties->setHorizontalHeaderLabels({QStringLiteral("Key"), QStringLiteral("Value"), QStringLiteral("Type")});
    m_properties->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Interactive);
    m_properties->setColumnWidth(0, 95);
    m_properties->horizontalHeader()->setMinimumSectionSize(55);
    m_properties->horizontalHeader()->setSectionResizeMode(1, QHeaderView::Stretch);
    m_properties->horizontalHeader()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    m_properties->verticalHeader()->hide();
    m_properties->setAlternatingRowColors(true);
    m_properties->setItemDelegateForColumn(1, new PropertyDelegate(m_properties));
    propertyLayout->addWidget(m_properties, 1);
    auto *propertyActions = new QHBoxLayout;
    m_addProperty = new QPushButton(QStringLiteral("Add field…"), propertyPanel);
    m_addProperty->setObjectName(QStringLiteral("visualAddProperty"));
    m_removeProperty = new QPushButton(QStringLiteral("Remove field"), propertyPanel);
    m_removeProperty->setObjectName(QStringLiteral("visualRemoveProperty"));
    propertyActions->addWidget(m_addProperty);
    propertyActions->addWidget(m_removeProperty);
    propertyLayout->addLayout(propertyActions);
    auto *hint = new QLabel(QStringLiteral("Double-click a value to edit. Objects and arrays open a JSON editor."), propertyPanel);
    hint->setWordWrap(true);
    propertyLayout->addWidget(hint);
    m_splitter->setSizes({200, 620, 330});
    m_splitter->setStretchFactor(0, 0);
    m_splitter->setStretchFactor(1, 1);
    m_splitter->setStretchFactor(2, 0);
    layout->addWidget(m_splitter, 1);

    connect(m_canvas, &QTreeWidget::currentItemChanged, this, [this] {
        if (!m_refreshing) { m_selection = selectedPath(); showProperties(); updateControls(); }
    });
    connect(m_properties, &QTableWidget::cellChanged, this, &VisualDesigner::applyPropertyEdit);
    connect(m_properties, &QTableWidget::cellDoubleClicked, this, [this](int row, int column) {
        if (column == 1) { editStructuredProperty(row); }
    });
    connect(m_properties, &QTableWidget::itemSelectionChanged, this, &VisualDesigner::updateControls);
    connect(m_paletteSearch, &QLineEdit::textChanged, this, &VisualDesigner::filterPalette);
    connect(m_canvasSearch, &QLineEdit::textChanged, this, &VisualDesigner::filterCanvas);
    connect(m_propertySearch, &QLineEdit::textChanged, this, &VisualDesigner::showProperties);
    connect(m_palette, &QListWidget::currentItemChanged, this, [this] {
        const auto *item = m_palette->currentItem();
        m_addComponent->setEnabled(item != nullptr && !item->isHidden());
        if (item != nullptr) { m_componentDescription->setText(item->toolTip()); }
    });
    connect(m_palette, &QListWidget::itemDoubleClicked, this, [this](QListWidgetItem *item) { insertTemplate(item->data(Qt::UserRole).toByteArray()); });
    connect(m_addComponent, &QPushButton::clicked, this, [this] {
        if (const auto *item = m_palette->currentItem()) { insertTemplate(item->data(Qt::UserRole).toByteArray()); }
    });
    connect(root, &QPushButton::clicked, this, [this] { m_canvas->setCurrentItem(nullptr); m_selection.clear(); showProperties(); updateControls(); });
    connect(m_undo, &QPushButton::clicked, this, &VisualDesigner::undo);
    connect(m_redo, &QPushButton::clicked, this, &VisualDesigner::redo);
    connect(m_duplicate, &QPushButton::clicked, this, &VisualDesigner::duplicateSelection);
    connect(m_remove, &QPushButton::clicked, this, &VisualDesigner::removeSelection);
    connect(m_moveUp, &QPushButton::clicked, this, &VisualDesigner::moveSelectionUp);
    connect(m_moveDown, &QPushButton::clicked, this, &VisualDesigner::moveSelectionDown);
    connect(m_addProperty, &QPushButton::clicked, this, &VisualDesigner::addProperty);
    connect(m_removeProperty, &QPushButton::clicked, this, &VisualDesigner::removeProperty);
    auto *undoShortcut = new QShortcut(QKeySequence::Undo, this);
    undoShortcut->setContext(Qt::WidgetWithChildrenShortcut);
    connect(undoShortcut, &QShortcut::activated, this, &VisualDesigner::undo);
    auto *redoShortcut = new QShortcut(QKeySequence::Redo, this);
    redoShortcut->setContext(Qt::WidgetWithChildrenShortcut);
    connect(redoShortcut, &QShortcut::activated, this, &VisualDesigner::redo);
    installBuiltInComponents();
    setDocument({});
}

void VisualDesigner::installBuiltInComponents()
{
    addPaletteComponent(QStringLiteral("SQL Resource"), QStringLiteral("resources"),
        {{QStringLiteral("database"), QStringLiteral("primary")}, {QStringLiteral("table"), QStringLiteral("items")},
         {QStringLiteral("path"), QStringLiteral("items")}, {QStringLiteral("auto_create"), false},
         {QStringLiteral("columns"), QJsonObject{}},
         {QStringLiteral("allowed_actions"), QJsonArray{QStringLiteral("list"), QStringLiteral("read")}}});
    addPaletteComponent(QStringLiteral("Operation / RPC"), QStringLiteral("operations"),
        {{QStringLiteral("name"), QStringLiteral("operation.name")}, {QStringLiteral("method"), QStringLiteral("POST")},
         {QStringLiteral("permission"), QStringLiteral("operation.execute")},
         {QStringLiteral("statements"), QJsonArray{QJsonObject{{QStringLiteral("sql"), QStringLiteral("SELECT 1")},
                                                             {QStringLiteral("mode"), QStringLiteral("scalar")}}}}});
    addPaletteComponent(QStringLiteral("Static Data Source"), QStringLiteral("data_sources"),
        {{QStringLiteral("name"), QStringLiteral("public-info")}, {QStringLiteral("type"), QStringLiteral("static")},
         {QStringLiteral("public"), true}, {QStringLiteral("data"), QJsonObject{{QStringLiteral("status"), QStringLiteral("ok")}}}});
    addPaletteComponent(QStringLiteral("Realtime Channel"), QStringLiteral("event_channels"),
        {{QStringLiteral("name"), QStringLiteral("updates")}, {QStringLiteral("publish_permission"), QStringLiteral("events.publish")},
         {QStringLiteral("subscribe_permission"), QStringLiteral("events.subscribe")}});
    addPaletteComponent(QStringLiteral("HTTP Data Source"), QStringLiteral("data_sources"),
        {{QStringLiteral("name"), QStringLiteral("external-api")}, {QStringLiteral("type"), QStringLiteral("http")},
         {QStringLiteral("url"), QStringLiteral("https://example.com/api")}, {QStringLiteral("method"), QStringLiteral("GET")},
         {QStringLiteral("timeout_seconds"), 10}, {QStringLiteral("permission"), QStringLiteral("data.external.read")}});
    addPaletteComponent(QStringLiteral("JSON File Source"), QStringLiteral("data_sources"),
        {{QStringLiteral("name"), QStringLiteral("catalog")}, {QStringLiteral("type"), QStringLiteral("json_file")},
         {QStringLiteral("file"), QStringLiteral("data/catalog.json")}, {QStringLiteral("writable"), false},
         {QStringLiteral("permission"), QStringLiteral("data.catalog.read")}});
    m_palette->setCurrentRow(0);
}

void VisualDesigner::addPaletteComponent(const QString &label, const QString &collection, const QJsonObject &value)
{
    const QJsonObject payload{{QStringLiteral("collection"), collection}, {QStringLiteral("value"), value}};
    auto *item = new QListWidgetItem(label + u'\n' + collection, m_palette);
    item->setSizeHint(QSize(110, 62));
    item->setToolTip(QStringLiteral("%1 — adds a new entry to %2. Double-click, use Add component, or drag into the structure.").arg(label, collection));
    item->setData(Qt::UserRole, QJsonDocument(payload).toJson(QJsonDocument::Compact));
    if (m_paletteSearch != nullptr) { filterPalette(); }
}

void VisualDesigner::setDocument(const QJsonObject &document)
{
    if (m_document == document && !m_history.isEmpty()) { return; }
    m_document = document;
    m_selection.clear();
    m_history = {document};
    m_selections = {QStringList{}};
    m_historyIndex = 0;
    refreshCanvas();
}

QJsonObject VisualDesigner::document() const { return m_document; }
QStringList VisualDesigner::selectedPath() const
{
    const auto *item = m_canvas->currentItem();
    return item == nullptr ? QStringList{} : item->data(0, PathRole).toStringList();
}
QJsonValue VisualDesigner::selectedValue() const { return atPath(m_document, m_selection); }

void VisualDesigner::commit(const QJsonObject &document, const QStringList &selection)
{
    if (document == m_document) { return; }
    m_selections[m_historyIndex] = m_selection;
    while (m_history.size() > m_historyIndex + 1) { m_history.removeLast(); m_selections.removeLast(); }
    m_document = document;
    m_selection = selection;
    m_history.append(document);
    m_selections.append(selection);
    if (m_history.size() > 31) { m_history.removeFirst(); m_selections.removeFirst(); }
    m_historyIndex = m_history.size() - 1;
    refreshCanvas();
    emit documentChanged(m_document);
}

void VisualDesigner::replaceSelection(const QJsonValue &value)
{
    const auto changed = changedAt(m_document, m_selection, value);
    if (changed.isObject()) { commit(changed.toObject(), m_selection); }
}

void VisualDesigner::undo()
{
    if (m_historyIndex == 0) { return; }
    m_selections[m_historyIndex] = m_selection;
    --m_historyIndex;
    m_document = m_history.at(m_historyIndex);
    m_selection = m_selections.at(m_historyIndex);
    refreshCanvas();
    emit documentChanged(m_document);
}
void VisualDesigner::redo()
{
    if (m_historyIndex + 1 >= m_history.size()) { return; }
    ++m_historyIndex;
    m_document = m_history.at(m_historyIndex);
    m_selection = m_selections.at(m_historyIndex);
    refreshCanvas();
    emit documentChanged(m_document);
}

void VisualDesigner::refreshCanvas()
{
    m_refreshing = true;
    m_canvas->clear();
    int nodes = 0;
    QTreeWidgetItem *selected = nullptr;
    std::function<void(QTreeWidgetItem *, const QString &, const QJsonValue &, const QStringList &)> append;
    append = [this, &append, &nodes, &selected](QTreeWidgetItem *parent, const QString &label,
                                              const QJsonValue &value, const QStringList &path) {
        if (++nodes > 5000 || path.size() > 64) { return; }
        auto *item = new QTreeWidgetItem({label, typeName(value), summary(value)});
        if (parent != nullptr) { parent->addChild(item); } else { m_canvas->addTopLevelItem(item); }
        item->setData(0, PathRole, path);
        item->setToolTip(0, QStringLiteral("Document / ") + path.join(QStringLiteral(" / ")));
        item->setToolTip(2, summary(value));
        if (path == m_selection) { selected = item; }
        item->setExpanded(path.size() == 1);
        if (value.isObject()) {
            const auto object = value.toObject();
            for (const auto &key : object.keys()) {
                if (nodes >= 5000) { break; }
                append(item, key, object.value(key), path + QStringList{key});
            }
        } else if (value.isArray()) {
            const auto array = value.toArray();
            for (qsizetype index = 0; index < array.size() && nodes < 5000; ++index) {
                const auto child = array.at(index);
                const auto name = child.isObject()
                    ? child.toObject().value(QStringLiteral("name")).toString(
                        child.toObject().value(QStringLiteral("path")).toString(QStringLiteral("Item %1").arg(index + 1)))
                    : QStringLiteral("[%1] · %2").arg(index).arg(summary(child));
                append(item, name, child, path + QStringList{QString::number(index)});
            }
        }
    };
    qsizetype components = 0;
    for (const auto &key : m_document.keys()) {
        append(nullptr, key, m_document.value(key), {key});
        if (m_document.value(key).isArray()) { components += m_document.value(key).toArray().size(); }
    }
    m_canvas->setCurrentItem(selected);
    if (selected != nullptr) {
        for (auto *parent = selected->parent(); parent != nullptr; parent = parent->parent()) { parent->setExpanded(true); }
        m_canvas->scrollToItem(selected);
    } else { m_selection.clear(); }
    m_summary->setText(QStringLiteral("%1 components · %2 document fields · Visual workspace").arg(components).arg(m_document.size()));
    m_feedback->setText(nodes > 5000 ? QStringLiteral("Outline limited to 5,000 fields. All document data is preserved; use the JSON editor for larger sections.")
                                   : QStringLiteral("Select any field to inspect it. Changes can be undone; arrays can be reordered."));
    m_refreshing = false;
    filterCanvas();
    showProperties();
    updateControls();
}

void VisualDesigner::showProperties()
{
    m_refreshing = true;
    m_properties->setRowCount(0);
    const auto value = selectedValue();
    m_selectionLabel->setText(m_selection.isEmpty() ? QStringLiteral("Document") : QStringLiteral("Document / ") + m_selection.join(QStringLiteral(" / ")));
    const auto filter = m_propertySearch->text().trimmed();
    const auto add = [this, &filter](const QString &key, const QJsonValue &field, bool scalar) {
        if (m_properties->rowCount() >= 2000) { return; }
        if (!filter.isEmpty() && !key.contains(filter, Qt::CaseInsensitive) && !summary(field).contains(filter, Qt::CaseInsensitive)) { return; }
        const auto row = m_properties->rowCount();
        m_properties->insertRow(row);
        auto *keyItem = new QTableWidgetItem(key);
        keyItem->setFlags(keyItem->flags() & ~Qt::ItemIsEditable);
        keyItem->setToolTip(key);
        keyItem->setData(ScalarRole, scalar);
        m_properties->setItem(row, 0, keyItem);
        auto *valueItem = new QTableWidgetItem(field.isObject() || field.isArray() ? summary(field) : valueText(field));
        valueItem->setData(ValueRole, QVariant::fromValue(field));
        if (field.isObject() || field.isArray()) { valueItem->setFlags(valueItem->flags() & ~Qt::ItemIsEditable); }
        valueItem->setToolTip(field.isObject() || field.isArray() ? QStringLiteral("Double-click to edit JSON") : valueText(field));
        m_properties->setItem(row, 1, valueItem);
        auto *type = new QTableWidgetItem(typeName(field));
        type->setFlags(type->flags() & ~Qt::ItemIsEditable);
        m_properties->setItem(row, 2, type);
    };
    if (value.isObject()) {
        const auto object = value.toObject();
        for (const auto &key : object.keys()) { add(key, object.value(key), false); }
    } else if (value.isArray()) {
        const auto array = value.toArray();
        for (qsizetype index = 0; index < array.size(); ++index) { add(QString::number(index), array.at(index), false); }
    } else if (!value.isUndefined()) { add(QStringLiteral("Value"), value, true); }
    m_refreshing = false;
    updateControls();
}

void VisualDesigner::applyPropertyEdit(int row, int column)
{
    if (m_refreshing || column != 1 || m_properties->item(row, 0) == nullptr || m_properties->item(row, 1) == nullptr) { return; }
    const auto original = m_properties->item(row, 1)->data(ValueRole).value<QJsonValue>();
    QJsonValue value;
    if (!parseValue(m_properties->item(row, 1)->text(), original, &value)) {
        emit statusMessage(QStringLiteral("Enter valid JSON of the existing property type. Text values stay text."));
        showProperties();
        return;
    }
    auto path = m_selection;
    if (!m_properties->item(row, 0)->data(ScalarRole).toBool()) { path.append(m_properties->item(row, 0)->text()); }
    commit(changedAt(m_document, path, value).toObject(), m_selection);
}

void VisualDesigner::insertTemplate(const QByteArray &payload)
{
    QJsonParseError error;
    const auto document = QJsonDocument::fromJson(payload, &error);
    if (error.error != QJsonParseError::NoError || !document.isObject()) { emit statusMessage(QStringLiteral("Component payload is invalid.")); return; }
    const auto collection = document.object().value(QStringLiteral("collection")).toString();
    auto value = document.object().value(QStringLiteral("value")).toObject();
    if (collection.isEmpty() || value.isEmpty()) { emit statusMessage(QStringLiteral("Component does not declare a target collection.")); return; }
    const auto existing = m_document.value(collection);
    if (!existing.isUndefined() && !existing.isArray()) { emit statusMessage(QStringLiteral("The target collection is not an array; existing data was preserved.")); return; }
    auto array = existing.toArray();
    value = uniqueComponent(value, array);
    array.append(value);
    auto changed = m_document;
    changed.insert(collection, array);
    commit(changed, {collection, QString::number(array.size() - 1)});
    emit statusMessage(QStringLiteral("Added %1 component.").arg(collection));
}

void VisualDesigner::updateControls()
{
    m_undo->setEnabled(m_historyIndex > 0);
    m_redo->setEnabled(m_historyIndex + 1 < m_history.size());
    auto parentPath = m_selection;
    const auto index = parentPath.isEmpty() ? -1 : parentPath.takeLast().toInt();
    const auto parent = atPath(m_document, parentPath);
    const bool arrayItem = !m_selection.isEmpty() && parent.isArray();
    m_duplicate->setEnabled(arrayItem);
    m_remove->setEnabled(!m_selection.isEmpty());
    m_moveUp->setEnabled(arrayItem && index > 0);
    m_moveDown->setEnabled(arrayItem && index + 1 < parent.toArray().size());
    m_addProperty->setEnabled(selectedValue().isObject() || selectedValue().isArray());
    m_addProperty->setText(selectedValue().isArray() ? QStringLiteral("Add item…") : QStringLiteral("Add field…"));
    m_removeProperty->setEnabled(m_properties->currentRow() >= 0 && !m_properties->item(m_properties->currentRow(), 0)->data(ScalarRole).toBool());
}

void VisualDesigner::duplicateSelection()
{
    auto path = m_selection;
    if (path.isEmpty()) { return; }
    const auto index = path.takeLast().toInt();
    const auto parent = atPath(m_document, path);
    if (!parent.isArray()) { return; }
    auto array = parent.toArray();
    if (index < 0 || index >= array.size()) { return; }
    auto copy = array.at(index);
    if (copy.isObject()) { copy = uniqueComponent(copy.toObject(), array); }
    array.insert(index + 1, copy);
    commit(changedAt(m_document, path, array).toObject(), path + QStringList{QString::number(index + 1)});
}
void VisualDesigner::removeSelection()
{
    if (m_selection.isEmpty()) { return; }
    auto parent = m_selection;
    parent.removeLast();
    commit(changedAt(m_document, m_selection, QJsonValue(QJsonValue::Undefined)).toObject(), parent);
}
void VisualDesigner::moveSelection(int direction)
{
    auto path = m_selection;
    if (path.isEmpty()) { return; }
    const auto index = path.takeLast().toInt();
    const auto parent = atPath(m_document, path);
    if (!parent.isArray()) { return; }
    auto array = parent.toArray();
    const auto target = index + direction;
    if (index < 0 || index >= array.size() || target < 0 || target >= array.size()) { return; }
    const auto value = array.at(index);
    array.removeAt(index);
    array.insert(target, value);
    commit(changedAt(m_document, path, array).toObject(), path + QStringList{QString::number(target)});
}
void VisualDesigner::moveSelectionUp() { moveSelection(-1); }
void VisualDesigner::moveSelectionDown() { moveSelection(1); }

void VisualDesigner::filterPalette()
{
    const auto query = m_paletteSearch->text().trimmed();
    for (int index = 0; index < m_palette->count(); ++index) {
        auto *item = m_palette->item(index);
        item->setHidden(!query.isEmpty() && !item->text().contains(query, Qt::CaseInsensitive));
    }
    if (m_addComponent != nullptr) { m_addComponent->setEnabled(m_palette->currentItem() != nullptr && !m_palette->currentItem()->isHidden()); }
}
void VisualDesigner::filterCanvas()
{
    const auto query = m_canvasSearch->text().trimmed();
    std::function<bool(QTreeWidgetItem *)> filter = [&filter, &query](QTreeWidgetItem *item) {
        bool match = query.isEmpty() || item->text(0).contains(query, Qt::CaseInsensitive) || item->text(2).contains(query, Qt::CaseInsensitive);
        for (int index = 0; index < item->childCount(); ++index) { match = filter(item->child(index)) || match; }
        item->setHidden(!match);
        if (match && !query.isEmpty()) { item->setExpanded(true); }
        return match;
    };
    for (int index = 0; index < m_canvas->topLevelItemCount(); ++index) { filter(m_canvas->topLevelItem(index)); }
}

void VisualDesigner::addProperty()
{
    const auto parent = selectedValue();
    if (!parent.isObject() && !parent.isArray()) { return; }
    QDialog dialog(this);
    dialog.setWindowTitle(parent.isArray() ? QStringLiteral("Add array item") : QStringLiteral("Add property"));
    auto *layout = new QVBoxLayout(&dialog);
    auto *form = new QFormLayout;
    QLineEdit name(&dialog);
    name.setObjectName(QStringLiteral("newPropertyName"));
    if (parent.isObject()) { form->addRow(QStringLiteral("Name"), &name); }
    else { name.hide(); }
    QComboBox type(&dialog);
    type.setObjectName(QStringLiteral("newPropertyType"));
    type.addItems({QStringLiteral("Text"), QStringLiteral("Number"), QStringLiteral("Boolean"), QStringLiteral("Object"), QStringLiteral("Array"), QStringLiteral("Null")});
    form->addRow(QStringLiteral("Type"), &type);
    layout->addLayout(form);
    QLabel error(&dialog);
    error.setObjectName(QStringLiteral("errorText"));
    layout->addWidget(&error);
    QDialogButtonBox buttons(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    layout->addWidget(&buttons);
    connect(&buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    connect(&buttons, &QDialogButtonBox::accepted, &dialog, [&] {
        if (parent.isObject() && (name.text().trimmed().isEmpty() || parent.toObject().contains(name.text().trimmed()))) {
            error.setText(QStringLiteral("Choose a nonempty, unique property name.")); return;
        }
        dialog.accept();
    });
    if (dialog.exec() != QDialog::Accepted) { return; }
    const QList<QJsonValue> values{QString{}, 0, false, QJsonObject{}, QJsonArray{}, QJsonValue(QJsonValue::Null)};
    if (parent.isObject()) {
        auto object = parent.toObject();
        object.insert(name.text().trimmed(), values.at(type.currentIndex()));
        replaceSelection(object);
    } else {
        auto array = parent.toArray();
        array.append(values.at(type.currentIndex()));
        replaceSelection(array);
    }
}

void VisualDesigner::removeProperty()
{
    const auto row = m_properties->currentRow();
    if (row < 0 || m_properties->item(row, 0)->data(ScalarRole).toBool()) { return; }
    const auto path = m_selection + QStringList{m_properties->item(row, 0)->text()};
    commit(changedAt(m_document, path, QJsonValue(QJsonValue::Undefined)).toObject(), m_selection);
}

void VisualDesigner::editStructuredProperty(int row)
{
    if (row < 0 || m_properties->item(row, 1) == nullptr) { return; }
    const auto original = m_properties->item(row, 1)->data(ValueRole).value<QJsonValue>();
    if (!original.isObject() && !original.isArray()) { return; }
    const auto path = m_selection + QStringList{m_properties->item(row, 0)->text()};
    QDialog dialog(this);
    dialog.setWindowTitle(QStringLiteral("Edit JSON · %1").arg(path.last()));
    auto *layout = new QVBoxLayout(&dialog);
    QPlainTextEdit editor(&dialog);
    editor.setObjectName(QStringLiteral("structuredPropertyEditor"));
    editor.setPlainText(QString::fromUtf8(original.isObject() ? QJsonDocument(original.toObject()).toJson(QJsonDocument::Indented)
                                                            : QJsonDocument(original.toArray()).toJson(QJsonDocument::Indented)));
    layout->addWidget(&editor, 1);
    QLabel error(&dialog);
    error.setWordWrap(true);
    error.setObjectName(QStringLiteral("errorText"));
    layout->addWidget(&error);
    QDialogButtonBox buttons(QDialogButtonBox::Save | QDialogButtonBox::Cancel, &dialog);
    layout->addWidget(&buttons);
    QJsonValue edited;
    connect(&buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    connect(&buttons, &QDialogButtonBox::accepted, &dialog, [&] {
        if (!parseValue(editor.toPlainText(), original, &edited)) {
            error.setText(QStringLiteral("Enter valid %1 JSON (maximum 8 MiB). Your text is kept for correction.").arg(typeName(original))); return;
        }
        dialog.accept();
    });
    ForgeEditorUi::resizeToFit(&dialog, QSize(680, 480));
    if (dialog.exec() == QDialog::Accepted) { commit(changedAt(m_document, path, edited).toObject(), m_selection); }
}

void VisualDesigner::resizeEvent(QResizeEvent *event)
{
    QWidget::resizeEvent(event);
    m_canvas->setColumnHidden(2, width() < 1050);
    m_properties->setColumnHidden(2, width() < 900);
    // On compact windows allocate enough space to the structure instead of
    // leaving it as a narrow strip between two fixed-size inspectors.
    if (width() < 1000) {
        m_splitter->setSizes({160, qMax(180, width() - 420), 250});
    }
}
