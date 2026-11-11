# Eksplorator plików

Lokalny menedżer plików w stylu Windows 11 dla KDE Plasma, napisany w PySide6.

## Uruchamianie

```bash
./fileexplorer                 # przywraca karty z poprzedniej sesji
./fileexplorer ~/Dokumenty     # otwiera wskazany folder
./fileexplorer --select plik   # otwiera folder i zaznacza plik
./fileexplorer --dbus          # rejestruje usługę org.freedesktop.FileManager1 bez okna
./fileexplorer --no-restore    # start bez przywracania sesji
FILEEXPLORER_TIMING=1 ./fileexplorer   # kamienie milowe startu na stderr
```

Wymagania: Python ≥ 3.11, PySide6 ≥ 6.5 (QtWidgets, QtDBus, QtSvg). Opcjonalnie: `gio`, `xdg-mime`, `gtk-launch` (otwieranie w aplikacjach), `konsole`, `kompare`/`meld`, KIOFuse (adresy `smb://`, `sftp://`), `kdeconnect-cli`, `bluedevil-sendfile`, `xdg-email`, `qrencode` (kod QR), [rquickshare](https://github.com/Martichou/rquickshare) (Quick Share; AUR: `r-quick-share`).

## Integracja z KDE i zastąpienie Dolphina

```bash
python3 install_integration.py          # instalacja w profilu użytkownika, bez sudo
python3 install_integration.py --check  # MIME, FileManager1, działający proces i Meta+E
python3 install_integration.py --restore ~/.local/state/FileExplorer/integration-backups/NAZWA_KOPII
```

Instalator zapisuje wpis aplikacji i akcje Folder osobisty / Ten komputer / Kosz, rejestruje aktywację obu usług D-Bus, ustawia domyślne `inode/directory` i `x-scheme-handler/trash`, autostart w tle oraz Meta+E przez KGlobalAccel. Pozostałe skojarzenia plików pozostają zachowane. Dawne skojarzenie `x-scheme-handler/file` z Eksploratorem jest usuwane, aby zwykłe pliki nadal otwierały właściwe aplikacje.

Przed zmianami powstaje kopia konfiguracji. Błąd instalacji uruchamia przywracanie kopii; konflikt Meta+E z aplikacją inną niż Dolphin jest wykrywany przed zapisem. Instalator nie kończy otwartych aplikacji. Po zmianie kodu działający proces trzeba uruchomić ponownie; prywatna metoda D-Bus `org.damian.FileExplorer.QuitIfIdle` na `/org/damian/FileExplorer` odmawia zakończenia podczas operacji plikowych, zadań w tle, aktywnej powłoki, udostępniania LAN lub dialogu modalnego.

Nie jest to jeszcze pełny odpowiednik wszystkich funkcji Dolphina: aktualna lista jest w [FEATURE_GAPS.md](FEATURE_GAPS.md).

## Funkcje

- Osobny podgląd (Alt+P): obrazy z zachowaniem proporcji, tekst do 256 KiB i pierwsza strona PDF (QtPdf, jeśli dostępne). Podgląd jest tylko do odczytu.
- Nośniki zamontowane i niezamontowane w pasku bocznym i „Ten komputer”: montowanie, odmontowanie i bezpieczne usuwanie przez UDisks/Polkit. Lista odświeża się w tle; partycje systemowe i dyski z aktywnym swap są chronione, a działająca operacja plikowa blokuje odłączanie. Wymagane `lsblk` i `udisksctl`.
- „Połącz z serwerem” (Ctrl+Shift+L), SMB/SFTP/FTP/MTP przez KIOFuse: montowanie w tle, limit oczekiwania, adresy sieciowe zachowane w historii i sesji. Nowe żądanie nawigacji unieważnia wynik wcześniejszego połączenia.
- Rozpakowywanie 7z i RAR przez systemową `libarchive`, z przygotowaniem w osobnym katalogu, kontrolą ścieżek i dowiązań. Archiwa wymagające hasła można obsługiwać przez Ark w menu „Otwórz w”.

- Karty (Ctrl+T/W, Ctrl+Shift+T przywraca zamkniętą; kartę można przeciągnąć na pasek innego okna, by scalić okna, albo poza okno, by ją odłączyć), widok podzielony (F3), wbudowany terminal (F4), panel szczegółów (Alt+Shift+P).
- Kopiowanie i przenoszenie w tle z paskiem postępu, anulowaniem, rozwiązywaniem konfliktów cofaniem (Ctrl+Z) i ponawianiem (Ctrl+Y).
- Kosz zgodny z XDG (Delete, bez pytania — Ctrl+Z cofa), trwałe usuwanie z potwierdzeniem (Shift+Delete), przywracanie, opróżnianie.
- Przeciąganie jak w Windows: w obrębie dysku przenosi, między dyskami kopiuje; Ctrl = kopiuj, Shift = przenieś, Alt = dowiązanie, Ctrl+Shift = menu. Etykieta „Przenieś do…” przy kursorze, po zakończeniu nowe elementy są zaznaczone, po usunięciu zaznaczany jest następny element. Kliknięcie w zaznaczoną nazwę rozpoczyna zmianę nazwy, w siatce ikon działa zaznaczanie prostokątem.
- Zmiana nazwy w miejscu jak w Windows (F2, zaznaczony trzon nazwy) i masowa (wzorzec z `#`), „Nowy folder” tworzony od razu z edycją nazwy, powielanie (Ctrl+D), dowiązania symboliczne, archiwa (zip/tar) i rozpakowywanie.
- Osiem trybów widoku jak w Windows 11 (Bardzo duże/Duże/Średnie/Małe ikony, Lista, Szczegóły, Kafelki, Zawartość; Ctrl+Shift+1…8, Ctrl+kółko), miniatury obrazów od średnich ikon wzwyż, galeria z miniaturami w tle, sortowanie naturalne (`plik2` przed `plik10`).
- Udostępnianie (Ctrl+Shift+S): KDE Connect, Quick Share (wymaga rquickshare), e-mail, Bluetooth, łącze w sieci lokalnej z kodem QR; foldery pakowane do ZIP.
- Menu kontekstowe w stylu Windows 11 (rząd ikon, „Pokaż więcej opcji”), szablony z folderu Szablony w menu „Nowy”, upuszczanie plików na pasek boczny.
- Pasek adresu z okruszkami ścieżki (strzałka przy segmencie rozwija jego podfoldery) i autouzupełnianiem (Ctrl+L), historia pod prawym przyciskiem na Wstecz/Dalej, przyciski myszy 4/5 = wstecz/dalej, wyszukiwanie w folderze i podfolderach w tle (Ctrl+F, obsługa `*.ext`), filtr typów (Ctrl+I).
- Tryb „Działaj w tle po zamknięciu” (menu ⋯): proces zostaje jako usługa D-Bus z ikoną w tacce systemowej (otwórz okno / zakończ) i startuje przy logowaniu, więc kolejne okna otwierają się natychmiast.
- Nowe okno (Ctrl+N), pełny ekran (F11), kopiowanie ścieżki (Ctrl+Shift+C), wyszukiwanie (Ctrl+E lub Ctrl+F).
- Motywy jasny / ciemny / OLED, zapamiętywanie sesji, geometrii, trybu widoku i sortowania.

## Ustawienia CachyOS

`settings_app.py` uruchamia osobne centrum ustawień KDE Plasma, także bez otwartego Eksploratora. Skrót w menu aplikacji użytkownika instaluje:

```bash
python install_settings.py
~/.local/bin/cachyos-fluent-settings
```

Menu zawiera kategorie w stylu Windows 11, wyszukiwarkę i pełny katalog modułów KCM wykrytych przez `kcmshell6 --list`. Każda pozycja otwiera dokładnie wskazany, zainstalowany moduł Plasma. Szybkie sterowanie Wi-Fi i siecią korzysta z NetworkManager (`nmcli`), Bluetooth z BlueZ (`bluetoothctl`), głośnością z PipeWire/WirePlumber (`wpctl`, ewentualnie `pactl`), profilami zasilania z `powerprofilesctl`, a czasem automatycznym z `timedatectl`. Zaawansowane formularze tych usług pozostają w natywnych modułach KDE.

Stan aktualizacji przy starcie pochodzi z lokalnej bazy `pacman -Qu`. Jeżeli dostępny jest `checkupdates`, aplikacja sprawdza repozytoria w tle przy otwarciu i co 10 minut; przycisk **Sprawdź online** pozwala powtórzyć to ręcznie. Źródło i czas sprawdzenia są pokazane przy wyniku. **Otwórz Discover** uruchamia natywny menedżer pakietów. Sprawdzenie online nie wykonuje aktualizacji ani nie zmienia systemowej bazy synchronizacji pacmana.

`python main.py --settings` nadal działa jako zgodny skrót do oddzielnej aplikacji. Narzędzia systemowe i moduły KCM są wykrywane w czasie działania; funkcje nieobecne w instalacji pozostają nieaktywne albo są pomijane.

## Testy

```bash
QT_QPA_PLATFORM=offscreen python test_explorer.py
QT_QPA_PLATFORM=offscreen python -m unittest -v test_settings.py test_file_operations.py test_archives.py test_share.py
QT_QPA_PLATFORM=offscreen python -m unittest -v test_applications.py test_linux_integration.py test_install_integration.py test_network.py test_devices.py test_archive_formats.py test_ui_regressions.py
python -m unittest -v test_dbus_activation.py
```

Testy regresji sprawdzają także konflikty cofania i ponawiania, częściowe błędy operacji, ochronę źródła przy błędzie odczytu podczas przenoszenia między dyskami, postęp powyżej 2 GB, kolizje nazw w jednej paczce, ochronę źródeł przed usunięciem przy nadpisywaniu folderu, pliki FIFO, zerwane dowiązania, anulowanie kopiowania i częściowe błędy kosza.

Test interfejsu tworzy katalog tymczasowy i przechodzi przez nawigację, kopiowanie, cofanie, zmianę nazwy, kosz, trwałe usuwanie, archiwa, karty, terminal i widok podzielony.

Operacje kopiowania przygotowują wynik w katalogu tymczasowym na dysku docelowym. Nadpisywanie podmienia istniejący element dopiero po przygotowaniu kompletnej kopii; anulowanie i błędy odczytu lub zapisu zachowują poprzedni cel. Ctrl+Z odmawia usunięcia kopii, jeśli jej metadane lub zawartość drzewa zmieniły się po operacji.

Rozpakowywanie najpierw sprawdza archiwum w osobnym katalogu. „Rozpakuj tutaj” odmawia zastąpienia istniejących elementów; przy kolizji należy wybrać rozpakowanie do osobnego folderu. Testy obejmują też błędy CRC, dowiązania wychodzące poza folder oraz częściowe niepowodzenie paczki archiwów.

Pakowanie do udostępniania zachowuje puste foldery, podąża za dowiązaniami do folderów i odrzuca ich pętle. Anulowanie jest sprawdzane także podczas odczytu dużego pliku; nieudana paczka jest usuwana. Każda paczka ma osobną nazwę, także przy równoczesnym pakowaniu. Nieudane opróżnienie kosza zachowuje metadane i historię pozostałych plików oraz zgłasza błąd.

Kopiowanie sprawdza całe drzewo źródła przed publikacją, aby przenoszenie między dyskami nie usuwało zmian powstałych w czasie odczytu. Jawne nadpisanie jest oznaczane w historii jako nieodwracalne: Ctrl+Z zachowuje nadpisany plik i wyjaśnia ograniczenie. Nie ma jeszcze magazynu poprzednich wersji nadpisanych celów.
