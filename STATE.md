# STATE — FileExplorer
Aktualizacja: 2026-09-30

## Cel
Lokalny eksplorator plików w stylu Windows 11 (PySide6, KDE/Linux) z integracją D-Bus `org.freedesktop.FileManager1`, koszem XDG, kartami, widokiem podzielonym i wbudowanym terminalem.

## Stos i uruchamianie
- Python 3.14, PySide6 6.11 (QtWidgets, QtDBus, QtSvg); brak requirements.txt — zależności systemowe.
- Start: `./fileexplorer [ścieżki] [--select] [--dbus] [--no-restore] [--screenshot plik.png]`
- Testy: `QT_QPA_PLATFORM=offscreen python test_explorer.py` (operacje na plikach w katalogu tymczasowym, własny XDG_CONFIG_HOME)
- Lint: `uvx pyflakes main.py icons.py linux_integration.py`

## Mapa (tylko to, co nieoczywiste)
- `main.py` — całe GUI. Kluczowe: stałe/pomocnicze `unique_path()`, `rename_noreplace()`, `alive()` (~55–120); `Icons` (cache ikon per rozszerzenie); `FileFilter` (proxy: filtr, sort naturalny `QCollator`, asynchroniczne miniatury `ThumbnailJob`); `Page` (jedna karta/panel; `navigate()`, `start_search()`/`add_results()`); `TransferJob` (kopiowanie/przenoszenie z postępem, anulowaniem, symlinkami, EXDEV); `Explorer` (okno: `run_transfer()`, `undo()`, `trash()`, `save_session()`/`restore_tabs()`, `context_menu()`, `properties()`).
- `linux_integration.py` — `FileManagerService` (D-Bus ShowFolders/ShowItems), `local_path()`, `mount_remote()` (KIOFuse).
- `share.py` — backendy udostępniania (`targets()`, `ShareServer`, `pack_paths()`, `quickshare_command()`); dialog: `ShareDialog` w `main.py`.
- `icons.py` — ikony SVG (`GlyphIcon` z cache pixmap per rozmiar/kolor), `assets/*.ico` nadpisują nazwy.
- `style.qss` — szablon `string.Template`, kolory z `THEMES` w `main.py`.

## W toku
- brak

## Zrobione (ostatnie, max 5)
- 2026-09-30 integracja i funkcje Dolphina: install_integration.py (kopie/rollback, MIME folderów i Kosza, aktywacja D-Bus, Meta+E), osobny podgląd Alt+P (preview_panel.py), nośniki lsblk/UDisks (devices.py), asynchroniczne połączenia KIOFuse (network.py), 7z/RAR (archive_formats.py). Naprawione ujawnianie ukrytych plików, właściwości D-Bus, skróty terminala, zaznaczanie i przeciąganie, cache aplikacji, publikacja archiwów i ochrona zmieniającego się źródła. Jawne overwrite blokuje destrukcyjne undo. Działająca instancja KDE wczytała poprawki. Braki i granice testów: FEATURE_GAPS.md.
- 2026-09-14 ponów i okruszki: `Explorer.redo_stack` + `apply_history(source, target, redo)` (jeden applier dla Ctrl+Z i Ctrl+Y: transfer/trash/rename w obie strony), `record()` czyści stos ponawiania przy nowej operacji, `replayable()` odrzuca wpisy bez źródła (archiwa), `write_trashinfo()` wspólny dla kosza i ponowienia; Ctrl+Y i Ctrl+Shift+Z, ikona `redo` w menu ⋯ i rzędzie ikon. Okruszki ścieżki: `crumb_arrow()`/`crumb_menu()` — strzałka między segmentami rozwija podfoldery (do 60, bieżąca gałąź zaznaczona), także na końcu ścieżki.
- 2026-09-14 otwieranie plików naprawione: `QDesktopServices.openUrl()`, `gio open` i `xdg-open` zwracają sukces, ale na tej sesji KDE nic nie uruchamiają (dwuklik na zdjęciu nie otwierał nic). `Explorer.launch_paths()` uruchamia rozwiązany wpis .desktop przez `gtk-launch` (fallback: `gio launch`, `gio open`, `xdg-open`, `openUrl`); `desktop_entries()` trzyma ścieżkę wpisu w `X-Entry-Path`; `Page.open_path()` i `open_with_application()` idą przez to. Enter otwiera wszystkie zaznaczone (foldery w kartach, pliki grupowane po MIME). Dodane Ctrl+N, Ctrl+Shift+C, Ctrl+E, F11 oraz wymiary obrazu w panelu szczegółów.
- 2026-09-08 karty jak Win11: `TabBar` (własna klasa, `setMovable(False)`) — pasek `QSizePolicy.Fixed` (dokładnie szerokość kart), przestawianie po slotach (`moveTab` wg `tabAt`), wyjście kursora poza pasek (36 px pion / 140 px bok) startuje `QDrag` z `TAB_MIME` (JSON pid/window/location); `Explorer.drag_tab()`/`receive_tab()` — upuszczenie na pasek innego okna przenosi kartę (i zamyka źródło, ostatnia karta = scalenie okien), upuszczenie poza oknem = nowe okno (`new_window()`, rejestr `WINDOWS`).
- 2026-09-07 tryby widoku jak Win11: `VIEW_MODES` (xlarge/large/medium/small/list/details/tiles/content, Ctrl+Shift+1…8, Ctrl+kółko cykl), `Page.configure_grid()` przestawia jeden `QListView` (viewMode/flow/wrapping/gridSize/delegat), `TileDelegate` i `ContentDelegate` (dziedziczą z `NameDelegate`, więc F2 działa), `Explorer.fill_view_modes()` w menu Wyświetl i kontekstowym; stare ustawienie `icons` → `large`.
- 2026-09-07 zachowania Win11: zmiana nazwy w miejscu (`NameDelegate` zaznacza trzon; model `setReadOnly(False)`, `fileRenamed` → undo), „Nowy folder/dokument/szablon” tworzone od razu (`numbered_path()`: „Nowy folder (2)”) + `Page.edit_when_available()`; Delete bez pytania (Ctrl+Z); upuszczanie: `Explorer.drop_action()` (Ctrl kopiuj, Shift przenieś, Alt dowiązanie, Ctrl+Shift menu, domyślnie przenieś w obrębie wolumenu); `FileModel.removeRows()` zwraca False (widok nie kasuje plików po MoveAction); Backspace = wstecz. Etykieta przy kursorze podczas przeciągania (`DropHint`, okno ToolTip, `Explorer.show_drop_hint()` z DragMove w `Page.eventFilter` i pasku bocznym) i zaznaczanie nowych wpisów po transferze (`Explorer.select_results()` → `Page.select_paths()` z ponawianiem; wywoływane po `refresh()`, bo `navigate()` czyści zaznaczenie). Po usunięciu zaznaczany następny wpis (`Page.neighbour_of_selection()` + `Explorer.select_successor()`), kliknięcie w zaznaczoną nazwę = edycja (`SelectedClicked`), prostokąt zaznaczenia w siatce (`setSelectionRectVisible`), przyciski myszy 4/5.

## Decyzje
- `QRunnable` zawsze z `setAutoDelete(False)` i referencją w `Explorer.background_jobs` / `FileFilter.pending` — bo Python zwalniał obiekt, gdy pula wątków jeszcze go używała (segfault).
- Menu kontekstowe przez `Explorer.menu()` + `popup()` (WA_DeleteOnClose) zamiast `exec()` — bo `QMenu(self)` nigdy nie było zwalniane.
- `release_clipboard()` przy `aboutToQuit` — bo Qt kasuje pythonowy `QMimeData` po finalizacji interpretera.

## Pułapki
- Tryb w tle: `set_keep_running()` zapisuje `keep_running` (+`sync()`), tworzy autostart i ikonę w tacce (`update_tray()`, `TRAY`); `open_window()` korzysta z `app.property('service')`. `--dbus` przy zajętej usłudze kończy się cicho (bez forwardu „home”).
- Quick Share: telefon rozgłasza `_FC9F5ED42C8A._tcp` z TXT `n=` (base64url: bajt0 bity 5–7 wersja, bit 4 „ukryty”, bity 1–3 typ; od bajtu 17 długość+nazwa). Bez nazwy = widoczność inna niż „Wszyscy” → rquickshare go nie pokazuje. `share.nearby_quickshare()` (avahi-browse) diagnozuje to w `ShareDialog`.
- Dwa interfejsy (eno2 192.168.100.x, wlan0 192.168.50.x): telefon jest w Wi‑Fi, więc łącze LAN musi używać adresu wlan0 — `share.lan_addresses()` sortuje Wi‑Fi jako pierwsze, w dialogu jest wybór.
- ufw: default deny incoming; przepuszczone tylko KDE Connect. Odbieranie Quick Share z telefonu na PC wymaga otwarcia portu TCP rquickshare (losowy, np. 36115) — decyzja użytkownika.
- rquickshare (`/usr/bin/rquickshare`, pakiet r-quick-share-bin) ignoruje argumenty wiersza poleceń (`tauri_plugin_single_instance` z `_argv`), ale przyjmuje pliki upuszczone na okno (`onDragDropEvent` w HomePage.vue) — stąd `DragHandle` w `ShareDialog`; ponowne uruchomienie tylko podnosi istniejące okno.
- Pomiar startu: `FILEEXPLORER_TIMING=1 ./fileexplorer` drukuje kamienie milowe na stderr (`mark()` w `main.py`).
- `kdeconnect-cli -a --id-name-only` drukuje podsumowanie „Znaleziono N urządzeń” na stderr; parser filtruje po wzorcu id.
- `QDialogButtonBox` standardowe przyciski wracają do angielskich napisów mimo `setText` — używać `addButton('Zamknij', RejectRole)` albo tłumaczeń Qt.
- `fast-auditor` ma w definicji nieistniejący model; działa tylko z `model: sonnet`.
- `QItemSelectionModel.selectedRows()` wymaga zaznaczenia wszystkich kolumn — `QListView` (siatka) zaznacza tylko kolumnę 0; używać `selectedIndexes()`.
- Drop zawsze zgłasza źródłu `CopyAction` (`event.setDropAction`) — inaczej `QAbstractItemView.startDrag` woła `removeRows` po MoveAction. Edytor nazwy w widoku: `view.findChild(QLineEdit)` (test).
- Przyciski myszy 4/5: filtr zdarzeń na `QApplication` w `Explorer.eventFilter` (Qt.BackButton/ForwardButton, tylko gdy `obj.window() is self`), połykane we wszystkich fazach.
- `QListView.setItemDelegateForColumn(0)` ma pierwszeństwo przed `setItemDelegate` — delegaty trybów podmieniać kolumnowo. W `IconMode` z `gridSize` `sizeHint` delegata jest ignorowany.
- Edytor nazwy: `NameDelegate.updateEditorGeometry()` liczy prostokąt z `SE_ItemViewItemText` i daje pełną szerokość komórki — domyślny Qt w IconMode dawał ucięty pasek pod miniaturą (wyglądało jak zdublowany podpis). `setUniformItemSizes` tylko dla kafelków/zawartości — miniatury mają różne wysokości.
- `QListView.setViewMode()` ustawia `dragEnabled`/`acceptDrops` z `Movement.Static` na False — po każdej zmianie trybu włączać je ponownie (`Page.configure_grid()`), inaczej przeciąganie zamienia się w ramkę zaznaczenia, a upuszczanie na siatkę nie działa.
- Wayland: KWin podnosi istniejące okno tylko z tokenem aktywacji (`XDG_ACTIVATION_TOKEN`; Electron przez FileManager1 przekazuje pusty `startup_id`). `FileManagerService.show()` bez tokenu i przy nieaktywnym oknie otwiera nowe okno; z tokenem ustawia go w env przed `activateWindow()`; `forward_to_running` przekazuje token z env. Lista okien KWin: skrypt `workspace.windowList()` przez `qdbus6 org.kde.KWin /Scripting loadScript` + `journalctl`.
- Okna top-level bez rodzica są własnością Pythona — bez referencji (`WINDOWS`) GC je usuwa. Karta przenoszona między oknami przez lokalizację, nie przez widget (`Page.window.model` jest per okno).
- `QTabBar` (Qt 6.11) nie ogranicza pływającej karty do paska i nie da się odczytać jej pozycji (dziecko to zwykły `QWidget` o stałej geometrii) — dlatego własne przestawianie po slotach zamiast `setMovable(True)`.
- Uruchamianie aplikacji: na tym systemie działa tylko `gtk-launch <id>` i `gio launch <plik.desktop>`; `gio open`, `xdg-open`, `kde-open` i `QDesktopServices.openUrl()` cicho zawodzą. Każdy `Popen` musi mieć `start_new_session=True`.
- `QTest.mouseDClick()` nie dostarcza pierwszego Press, więc podwójne kliknięcie testuje się dopiero po `mouseClick()` — sam dwuklik w teście zawsze wygląda na zepsuty.
- Test w trybie offscreen: dialogi modalne zamyka timer `close_dialogs()` (pomija `ProgressDialog` i `ShareDialog`); `QInputDialog`/`QMessageBox` są monkeypatchowane.
- `Path('a.txt — x').suffix` zwraca `.txt — x` — nazwy dowiązań budować ręcznie, nie przez `unique_path()`.

## Otwarte pytania
- Aktualny zakres i brakujące funkcje względem Dolphina: FEATURE_GAPS.md. Panel podglądu Alt+P jest już zaimplementowany.
