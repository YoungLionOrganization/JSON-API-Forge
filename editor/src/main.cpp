#include "MainWindow.hpp"

#include <QApplication>
#include <QCommandLineOption>
#include <QCommandLineParser>
#include <QDebug>
#include <QIcon>
#include <QPropertyAnimation>
#include <QRegularExpression>
#include <QSize>
#include <QStyleFactory>
#include <QTimer>

int main(int argc, char *argv[])
{
    QApplication application(argc, argv);
    application.setApplicationDisplayName(QStringLiteral("JSON API Forge Editor"));
    application.setApplicationVersion(QStringLiteral("0.5.2"));
    application.setWindowIcon(QIcon(QStringLiteral(":/branding/logo.png")));
    application.setStyle(QStyleFactory::create(QStringLiteral("Fusion")));

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Policy-aware visual and code editor for JSON API Forge"));
    parser.addHelpOption();
    parser.addVersionOption();
    const QCommandLineOption screenshotOption(
        QStringList{QStringLiteral("screenshot")},
        QStringLiteral("Render the initial window to <path> and exit (visual regression/packaging smoke test)."),
        QStringLiteral("path"));
    parser.addOption(screenshotOption);
    const QCommandLineOption windowSizeOption(
        QStringList{QStringLiteral("window-size")},
        QStringLiteral("Set the initial window size to <WIDTHxHEIGHT> (720x480 through 7680x4320)."),
        QStringLiteral("WIDTHxHEIGHT"));
    parser.addOption(windowSizeOption);
    const QCommandLineOption graphPreviewOption(QStringList{QStringLiteral("graph-preview")},
                                                 QStringLiteral("Open the built-in operation graph preview."));
    parser.addOption(graphPreviewOption);
    const QCommandLineOption teamPreviewOption(QStringList{QStringLiteral("team-preview")},
                                                QStringLiteral("Open the server Team Workspace preview."));
    parser.addOption(teamPreviewOption);
    const QCommandLineOption visualPreviewOption(QStringList{QStringLiteral("visual-preview")},
                                                  QStringLiteral("Open the built-in visual workspace preview."));
    parser.addOption(visualPreviewOption);
    parser.process(application);

    const auto screenshotPath = parser.value(screenshotOption);
    const bool renderingPreview = !screenshotPath.isEmpty();
    QSize requestedSize;
    if (parser.isSet(windowSizeOption)) {
        const QRegularExpression pattern(QStringLiteral("^(\\d{3,4})x(\\d{3,4})$"));
        const auto match = pattern.match(parser.value(windowSizeOption));
        bool widthOk = false;
        bool heightOk = false;
        const int width = match.hasMatch() ? match.captured(1).toInt(&widthOk) : 0;
        const int height = match.hasMatch() ? match.captured(2).toInt(&heightOk) : 0;
        if (!widthOk || !heightOk || width < 720 || width > 7680 || height < 480 || height > 4320) {
            qCritical().noquote() << "--window-size must be WIDTHxHEIGHT between 720x480 and 7680x4320";
            return 2;
        }
        requestedSize = QSize(width, height);
    }

    MainWindow window(nullptr, !renderingPreview);
    if (requestedSize.isValid()) {
        window.resize(requestedSize);
    }
    if (parser.isSet(graphPreviewOption)) {
        window.showGraphPreview();
    }
    if (parser.isSet(teamPreviewOption)) {
        window.showTeamPreview();
    }
    if (parser.isSet(visualPreviewOption)) { window.showVisualPreview(); }
    window.setWindowOpacity(renderingPreview ? 1.0 : 0.0);
    window.show();
    if (renderingPreview) {
        QTimer::singleShot(500, &window, [&application, &window, screenshotPath] {
            application.exit(window.grab().save(screenshotPath, "PNG") ? 0 : 2);
        });
    } else {
        auto *startup = new QPropertyAnimation(&window, "windowOpacity", &window);
        startup->setDuration(320);
        startup->setStartValue(0.0);
        startup->setEndValue(1.0);
        startup->setEasingCurve(QEasingCurve::OutCubic);
        startup->start(QAbstractAnimation::DeleteWhenStopped);
    }
    return QApplication::exec();
}
