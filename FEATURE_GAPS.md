# Funkcje względem Dolphina — stan 2026-09-30

Explorer obsługuje podstawową pracę na plikach i jest domyślnym menedżerem w bieżącej sesji KDE. Pełny parytet funkcji nie jest jeszcze osiągnięty.

## Dodane i sprawdzone

| Obszar | Obecny zakres |
| --- | --- |
| Integracja KDE | Domyślne foldery i Kosz, Meta+E, menu i akcje aplikacji, autostart, aktywacja D-Bus, CLI przekazujące żądanie do jednej instancji |
| „Pokaż w folderze” | FileManager1 ShowItems, wiele plików w folderze, Unicode/spacje/%/#/?, ujawnianie wskazanego pliku ukrytego mimo filtra |
| Właściwości z innych aplikacji | ShowItemProperties otwiera właściwości każdej jawnie przekazanej ścieżki, także folderu |
| Podgląd | Oddzielny Alt+P, obrazy, ograniczony tekst, pierwsza strona PDF, metadane |
| Nośniki | lsblk w tle, niezamontowane woluminy, UDisks mount/unmount/power-off, ochrona partycji systemowych i aktywnego swap, zachowane montowania Windows |
| Sieć | Dialog połączenia, KIOFuse w tle, SMB/SFTP/FTP/MTP, limit oczekiwania i zachowanie URI w adresie/historii/sesji |
| Archiwa | Tworzenie ZIP/TAR; przygotowanie i publikacja; rozpakowywanie ZIP/TAR/7z/RAR oraz dowiązań pozostających w folderze |
| Operacje i interfejs | Karty, podział, kopiowanie/przenoszenie, konflikty, kosz, undo/redo, masowa zmiana nazwy, szablony, filtry, miniatury, wyszukiwanie nazw, przeciąganie do aplikacji |

## Pozostałe funkcje i ograniczenia

1. **Pełny terminal PTY.** F4 wykonuje polecenia w bash przez QProcess; programy interaktywne i kontrola terminala wymagają prawdziwego emulatora. „Otwórz w terminalu” uruchamia Konsole i zapewnia te funkcje obecnie.
2. **Wyszukiwanie po treści, tagach i metadanych.** Aktualne wyszukiwanie przegląda nazwy rekursywnie. Brakuje integracji Baloo, zapisanych wyszukiwań i tagowania.
3. **Menu usług KDE oraz Git/SVN.** Nie ma ogólnego ładowania service menus, odznak stanu repozytorium ani zestawu akcji wersjonowania.
4. **Kosz per wolumin.** Obecny widok i operacje używają kosza w katalogu danych użytkownika. Brakuje agregowania koszy z innych woluminów i obsługi `.Trash` / `.Trash-$uid`; usuwanie z innego dysku może wymagać kopiowania do kosza domowego.
5. **Odtwarzanie nadpisanych wersji.** Jawne nadpisanie nie przechowuje poprzedniego celu. Cofanie jest dla takiej pozycji bezpiecznie odmawiane; potrzebny jest zarządzany magazyn backupów z poprawną obsługą redo.
6. **Dodatkowe podglądy.** Audio/wideo, dokumenty biurowe, większa część PDF i podgląd zawartości archiwów wymagają rozszerzenia. Obecne dekodowanie podglądu odbywa się w GUI, więc powolny zdalny plik może opóźnić podgląd.
7. **Zaawansowane nośniki i udziały.** Odblokowywanie LUKS, formatowanie, udostępnianie folderów Samba i zaawansowane ACL nie są zaimplementowane.
8. **Pełny stan przenoszonej karty.** Przeciągnięcie karty między oknami przenosi lokalizację, ale nie pełną historię, zaznaczenie i stan podziału.
9. **Uprzywilejowane operacje.** Przycisk uruchamiania administratora nie stanowi pełnego odpowiednika integracji `admin://`/KIO; nie został zweryfikowany jako przepływ operacji na chronionych plikach.

## Zakres weryfikacji

Testy sprawdzają lokalne operacje w katalogach tymczasowych, urządzenia na fixture/mock, sieć na kontrolowanych montowaniach i rzeczywistą aktywację FileManager1 na osobnej sesji D-Bus. W normalnej sesji KDE potwierdzono domyślne MIME, właściciela FileManager1 oraz Meta+E. Nie odmontowywano fizycznych nośników, nie formatowano ich i nie testowano rzeczywistego serwera SMB/SFTP ani logowania po ponownym uruchomieniu systemu.
