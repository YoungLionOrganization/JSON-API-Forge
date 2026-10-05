cmake_minimum_required(VERSION 3.24)
project(JSONAPIForgeEditor VERSION 0.5.2 LANGUAGES C CXX)

set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

set(FORGE_EDITOR_QT_MIN_VERSION "6.4" CACHE STRING "Minimum supported Qt version")
find_package(Qt6 ${FORGE_EDITOR_QT_MIN_VERSION} REQUIRED COMPONENTS Core Gui Widgets Network)
find_package(Qt6 ${FORGE_EDITOR_QT_MIN_VERSION} QUIET COMPONENTS WebEngineWidgets)
option(FORGE_EDITOR_WARNINGS_AS_ERRORS "Treat editor compiler warnings as errors" OFF)
option(FORGE_EDITOR_ENABLE_SANITIZERS "Enable AddressSanitizer and UndefinedBehaviorSanitizer" OFF)

function(forge_editor_enable_warnings target)
  if(MSVC)
    target_compile_options(${target} PRIVATE /W4 /permissive- /EHsc)
  else()
    target_compile_options(${target} PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
  endif()
  if(FORGE_EDITOR_WARNINGS_AS_ERRORS)
    if(MSVC)
      target_compile_options(${target} PRIVATE /WX)
    else()
      target_compile_options(${target} PRIVATE -Werror)
    endif()
  endif()
  if(NOT MSVC)
    target_compile_options(${target} PRIVATE -fstack-protector-strong)
  endif()
  if(FORGE_EDITOR_ENABLE_SANITIZERS)
    if(MSVC)
      message(FATAL_ERROR "FORGE_EDITOR_ENABLE_SANITIZERS is supported by the GCC/Clang CI kit")
    endif()
    target_compile_options(${target} PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
    get_target_property(forge_editor_target_type ${target} TYPE)
    if(NOT forge_editor_target_type STREQUAL "STATIC_LIBRARY")
      target_link_options(${target} PRIVATE -fsanitize=address,undefined)
    endif()
  endif()
endfunction()

add_library(forge_zip_inflate STATIC third_party/puff/puff.c)
target_include_directories(forge_zip_inflate PUBLIC third_party/puff)
if(FORGE_EDITOR_ENABLE_SANITIZERS AND NOT MSVC)
  target_compile_options(forge_zip_inflate PRIVATE -fsanitize=address,undefined -fno-omit-frame-pointer)
endif()

add_library(forge_editor_core STATIC
  src/SafeZipReader.cpp
  src/SafeZipReader.hpp
  src/ApiClient.cpp
  src/ApiClient.hpp
  src/CodeEditor.cpp
  src/CodeEditor.hpp
  src/ConnectionDialog.cpp
  src/ConnectionDialog.hpp
  src/DocumentCodec.cpp
  src/DocumentCodec.hpp
  src/EditorSettings.cpp
  src/EditorSettings.hpp
  src/GraphModel.cpp
  src/GraphModel.hpp
  src/JsonHighlighter.cpp
  src/JsonHighlighter.hpp
  src/NodeGraphEditor.cpp
  src/NodeGraphEditor.hpp
  src/PluginManager.cpp
  src/PluginManager.hpp
  src/PluginCatalogClient.cpp
  src/PluginCatalogClient.hpp
  src/PythonSdkPanel.cpp
  src/PythonSdkPanel.hpp
  src/TemplateManager.cpp
  src/TemplateManager.hpp
  src/TeamWorkspace.cpp
  src/TeamWorkspace.hpp
  src/ChatView.cpp
  src/ChatView.hpp
  src/UiSizing.hpp
  src/VisualDesigner.cpp
  src/VisualDesigner.hpp
  include/forgeeditor/IEditorPlugin.hpp
)
target_include_directories(forge_editor_core PUBLIC include src)
target_link_libraries(forge_editor_core
  PUBLIC Qt6::Core Qt6::Gui Qt6::Widgets Qt6::Network
  PRIVATE forge_zip_inflate
)
if(TARGET Qt6::WebEngineWidgets)
  target_link_libraries(forge_editor_core PUBLIC Qt6::WebEngineWidgets)
  target_compile_definitions(forge_editor_core PUBLIC FORGE_EDITOR_HAS_WEBENGINE=1)
endif()
target_compile_definitions(forge_editor_core PUBLIC QT_NO_CAST_FROM_ASCII QT_NO_CAST_TO_ASCII)

forge_editor_enable_warnings(forge_editor_core)

qt_add_executable(JSONAPIForgeEditor
  src/main.cpp
  src/MainWindow.cpp
  src/MainWindow.hpp
  resources/editor.qrc
)
target_link_libraries(JSONAPIForgeEditor PRIVATE forge_editor_core)
forge_editor_enable_warnings(JSONAPIForgeEditor)
if(WIN32)
  if(MSVC)
    target_compile_options(JSONAPIForgeEditor PRIVATE /sdl /guard:cf)
    target_compile_options(forge_editor_core PRIVATE /sdl /guard:cf)
    target_link_options(JSONAPIForgeEditor PRIVATE /guard:cf /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA)
  endif()
  target_sources(JSONAPIForgeEditor PRIVATE resources/windows.rc resources/forge-editor.ico)
elseif(APPLE)
  set_source_files_properties(resources/forge-editor.icns PROPERTIES MACOSX_PACKAGE_LOCATION "Resources")
  target_sources(JSONAPIForgeEditor PRIVATE resources/forge-editor.icns)
endif()
set_target_properties(JSONAPIForgeEditor PROPERTIES
  OUTPUT_NAME "JSON-API-Forge-Editor"
  WIN32_EXECUTABLE TRUE
  MACOSX_BUNDLE TRUE
  MACOSX_BUNDLE_BUNDLE_NAME "JSON API Forge Editor"
  MACOSX_BUNDLE_GUI_IDENTIFIER "dev.jsonapiforge.editor"
  MACOSX_BUNDLE_ICON_FILE "forge-editor.icns"
  MACOSX_BUNDLE_SHORT_VERSION_STRING "${PROJECT_VERSION}"
)

install(TARGETS JSONAPIForgeEditor
  BUNDLE DESTINATION .
  RUNTIME DESTINATION bin
)
install(FILES third_party/puff/puff.h third_party/puff/README.md DESTINATION licenses/puff)
install(DIRECTORY plugins/ DESTINATION plugins OPTIONAL)
install(FILES
  "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE"
  "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE-FAQ.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/../NOTICE.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/../THIRD_PARTY_NOTICES.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/../TRADEMARK_POLICY.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/../AI_USAGE_POLICY.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE-HISTORY.md"
  "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE-METADATA.json"
  DESTINATION .
)
install(DIRECTORY packaging/licenses/ DESTINATION licenses/qt)
if(UNIX AND NOT APPLE)
  install(FILES resources/dev.jsonapiforge.editor.desktop DESTINATION share/applications)
  install(FILES resources/logo.png
    DESTINATION share/icons/hicolor/512x512/apps
    RENAME dev.jsonapiforge.editor.png
  )
endif()

if(BUILD_TESTING)
  find_package(Qt6 ${FORGE_EDITOR_QT_MIN_VERSION} REQUIRED COMPONENTS Test)
  qt_add_executable(forge_editor_tests tests/tst_editor_core.cpp resources/editor.qrc)
  target_link_libraries(forge_editor_tests PRIVATE forge_editor_core Qt6::Test)
  set_target_properties(forge_editor_tests PROPERTIES WIN32_EXECUTABLE FALSE MACOSX_BUNDLE FALSE)
  forge_editor_enable_warnings(forge_editor_tests)
  if(MSVC)
    target_compile_options(forge_editor_tests PRIVATE /sdl /guard:cf)
    target_link_options(forge_editor_tests PRIVATE /guard:cf /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA)
  elseif(UNIX AND NOT APPLE)
    target_link_options(forge_editor_tests PRIVATE -Wl,-z,relro -Wl,-z,now)
  endif()
  add_test(NAME forge_editor_core COMMAND forge_editor_tests -o -,txt -o "${CMAKE_CURRENT_BINARY_DIR}/editor-tests.xml,junitxml")
  set_tests_properties(forge_editor_core PROPERTIES TIMEOUT 120 ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
  qt_add_executable(forge_editor_ui_tests tests/tst_editor_ui.cpp src/MainWindow.cpp src/MainWindow.hpp resources/editor.qrc)
  target_link_libraries(forge_editor_ui_tests PRIVATE forge_editor_core Qt6::Test)
  set_target_properties(forge_editor_ui_tests PROPERTIES WIN32_EXECUTABLE FALSE MACOSX_BUNDLE FALSE)
  forge_editor_enable_warnings(forge_editor_ui_tests)
  if(MSVC)
    target_compile_options(forge_editor_ui_tests PRIVATE /sdl /guard:cf)
    target_link_options(forge_editor_ui_tests PRIVATE /guard:cf /DYNAMICBASE /NXCOMPAT /HIGHENTROPYVA)
  elseif(UNIX AND NOT APPLE)
    target_link_options(forge_editor_ui_tests PRIVATE -Wl,-z,relro -Wl,-z,now)
  endif()
  add_test(NAME forge_editor_ui COMMAND forge_editor_ui_tests -o -,txt -o "${CMAKE_CURRENT_BINARY_DIR}/editor-ui-tests.xml,junitxml")
  set_tests_properties(forge_editor_ui PROPERTIES TIMEOUT 120 ENVIRONMENT "QT_QPA_PLATFORM=offscreen")
endif()

if(UNIX AND NOT APPLE)
  target_link_options(JSONAPIForgeEditor PRIVATE -Wl,-z,relro -Wl,-z,now)
endif()

set(CPACK_PACKAGE_NAME "JSON-API-Forge-Editor")
set(CPACK_PACKAGE_VENDOR "Cavanshirpro")
set(CPACK_PACKAGE_VERSION "${PROJECT_VERSION}")
set(CPACK_PACKAGE_DESCRIPTION_SUMMARY "Policy-aware visual and code editor for JSON API Forge")
set(CPACK_RESOURCE_FILE_LICENSE "${CMAKE_CURRENT_SOURCE_DIR}/../LICENSE")
set(CPACK_GENERATOR "ZIP")
include(CPack)
