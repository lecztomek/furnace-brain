installation
pip install -r requirements.txt

dev run
./furnace-run-dev.sh 

tests
python -m pip install -U pytest
python -m pytest -q tests/test_stats_module.py

deploy
======

Jak to działa
- main  -> środowisko testowe w chmurze (Render, symulator pieca), wdraża się samo
           po każdym pushu, który przejdzie testy (render.yaml, .github/workflows/tests.yml)
- prod  -> prawdziwy piec. Pi co 5 min sprawdza branch prod (furnace-update.timer)
           i sam robi deploy/update.sh: kopia values.yaml, restart, sprawdzenie
           czy backend wstał, a jak nie - powrót do poprzedniej wersji.

Wydanie na piec / powrót do starszej wersji
- GitHub -> Actions -> "Wdróż na prod" -> Run workflow
  - version = main                  -> wydaj to, co jest teraz na main
  - version = prod-20261026-120000  -> powrót do wcześniejszego wdrożenia
  - version = <hash commita>        -> dowolna wersja
- każde wdrożenie dostaje tag prod-RRRRMMDD-GGMMSS, lista ostatnich jest
  w podsumowaniu każdego uruchomienia workflow
- wersja, która nie wstała, jest zapamiętywana i pomijana, dopóki prod się nie zmieni

Ustawienia (backend/modules/*/values.yaml) nie są w gicie i deploy ich nie rusza.
Nowe pola ze schema.yaml backend sam dopisuje na końcu values.yaml przy starcie.

Na Pi - ręcznie
  deploy/update.sh --dry-run     # co by się zmieniło
  deploy/update.sh --rollback    # awaryjnie: poprzednia wersja (do następnej zmiany prod)
  deploy/update.sh --force       # ponów wersję, która wcześniej nie wstała
  journalctl -u furnace-update -e   # logi automatycznych aktualizacji

Pierwsze przejście na git (jednorazowo, na Pi)
  sudo apt install -y git
  cd /home/pi/furnace-brain
  git init -b main
  git remote add origin https://github.com/lecztomek/furnace-brain.git
  git fetch origin
  git reset origin/prod          # pliki na dysku zostają, zmienia się tylko indeks
  git status                     # czym Pi różni się od repo - przejrzyj!
  git diff
  git checkout -- .              # gdy różnice można wyrzucić
  git checkout -B prod origin/prod

  sudo cp deploy/furnace-backend.service deploy/furnace-gateway.service \
          deploy/furnace-update.service deploy/furnace-update.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl restart furnace-backend furnace-gateway
  sudo systemctl enable --now furnace-update.timer

Pierwsza instalacja usług (nowe Pi)
  create /etc/furnace-brain.env (wzór: deploy/furnace-brain.env.sample)
  python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
  sudo systemctl enable --now furnace-backend.service furnace-gateway.service

  sudo systemctl status furnace-backend.service --no-pager
  sudo systemctl status furnace-gateway.service --no-pager



TODO
ok- przeladowywanie ustawien po zapisie z gui
ok- gui ogien poprawa locaklizacji
ok- gui chowany pasek boczny
- ustawienia zaawansowane i uproszczone
ok- rozpalanie ui ustawia tryb rozpalania, przejscie do work tez powinno gdzies byc
ok- stop wylacza wszystko na off oprocz pomp
ok- modul logow
ok- modul zaworu mieszajacego
ok- modul historii - work, temp grzejniki, temp piec, spaliny (co 30 sek)
- modul czyszczenia historii
ok- modul statystyk - zuzycie wegla, spalanie na godzine, 
- blokada ui
- ustawienia smart - korekcja ustawien -> za duzo sadzy, za duzo popiolu, za duza temp spalin
ok- dokladnosc wyswietlania w ustawieniach float 0.01 
ok- sortowanie modulow w ustawieniach
ok- problem z zamykaniem aplikacji
ok- mixer osobno czasy w iginition i work oraz czasy miedzy korektami
ok- dmuchawa cos nie respektuje temp spalin
ok- poprawki ui - dmucawa nie pokazuj procentow, slimak nie pokazuj korekty
- uproszone menu ustawien -> temp pieca, temp grzejniki, temp przelaczanie rozpalanie-work,
 dmuchawa obroty, slimak bazowe nastawy
- w schame popraw zakresy parametrow - przejdz przez wszystkie moduly
ok- mixer algorytm rozpalania jesli daleko od zadanej na grzejnikach jesli blisko to algorytm standby - nie patrzy na ignition
ok- popraw power work tak zeby pid licxzyl caly czas a uzywal dopiero w trybie work
ok- pompa cwu nie reaguje na stop - powinna miec takie tryby jak pompa co
ok- zmiana czasu
ok- przyspieszone testy
- modul wygaszania, tryb wygaszania pieca, 
ok- tryy rozpalania - jak daleko grzejniki od zadanej to najpierw zamykjaj zawor
ok- modul bezpieczentwa - przegrzanie slimaka, pieca, brak odczytu temperatury  !!!
ok- poprawic event logi !!!
ok- testy automatyczne do kazdego modulu osobno ?
ok- statystki spalania maja isc do historii
- gui slabo dziala na telefonach (ustawienia, statystyki)
- uproszczone opcje
ok- zmiana czasu - tryb pid wariuje
ok- restart systemu - pid od zera poparwic
ok- ilosc spalonego wegla 
- przerob stats zeby bylo dwupoziomowe zapis w aux a wyliczanie w critical
arduino modul przekaznika 
