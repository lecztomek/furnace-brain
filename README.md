installation
pip install -r requirements.txt

dev run
./furnace-run-dev.sh 

tests
python -m pip install -U pytest
python -m pytest -q tests/test_stats_module.py

deploy
======

How it works
- main  -> cloud test environment (Render, simulated furnace), deployed automatically
           on every push that passes the tests (render.yaml, .github/workflows/tests.yml)
- prod  -> the real furnace. Every 5 min the Pi checks the prod branch (furnace-update.timer)
           and runs deploy/update.sh: back up values.yaml, restart, check that the
           backend came up - and if it didn't, roll back to the previous version.

Releasing to the furnace / going back to an older version
- GitHub -> Actions -> "Deploy to prod" -> Run workflow
  - version = main            -> release what is on main right now
  - version = v1.0.0          -> go back to an earlier release
  - version = <commit hash>   -> any version
- the released commit is tagged v<VERSION>; released versions are listed
  in the summary of each workflow run

Version numbers
- the VERSION file holds the version (X.Y.Z), shown on the main page
- bump it with every change: Z for fixes, Y for new features, X for big changes
- "Deploy to prod" refuses to release changed code under an already released
  version number (= VERSION was not bumped)
- a version that failed to start is remembered and skipped until prod changes

Remote view of the furnace (read-only)
- Render service furnace-brain-view (render.yaml) runs backend/viewer.py on the
  prod branch: the same GUI, data pushed by the Pi; every change is ignored
- on the Pi, /etc/furnace-brain.env:
    FURNACE_BRAIN_VIEWER_URL=https://furnace-brain-view.onrender.com
    FURNACE_BRAIN_VIEWER_TOKEN=<FURNACE_BRAIN_VIEWER_TOKEN from the Render dashboard>
  then restart furnace-backend and enable "Podgląd zdalny" in the settings
- the Pi only sends (gzip, every 15 s state / 60 s history and logs /
  10 min statistics and settings) - nothing comes back to the furnace
- optional viewing password: FURNACE_BRAIN_VIEWER_PASSWORD on Render (user "podglad")
- free Render plan: 750 h/month for the whole account - the view running
  24/7 uses almost all of it; switch it to a paid plan to keep the test
  environment running too

Settings (backend/modules/*/values.yaml) are not in git and deploys don't touch them.
On startup the backend appends new fields from schema.yaml to the end of values.yaml.

On the Pi - manually
  deploy/update.sh --dry-run     # what would change
  deploy/update.sh --rollback    # emergency: previous version (until the next prod change)
  deploy/update.sh --force       # retry a version that previously failed to start
  journalctl -u furnace-update -e   # automatic update logs

Switching the Pi to git (one-time, on the Pi; the prod branch must exist)
  sudo apt install -y git
  cd /home/pi/furnace-brain
  git init -b main
  git remote add origin https://github.com/lecztomek/furnace-brain.git
  git fetch origin
  git reset origin/prod          # files on disk stay as they are, only the index changes
  git status                     # how the Pi differs from the repo - review it!
  git diff
  git checkout -- .              # once the differences can be discarded
  git checkout -B prod origin/prod

  sudo cp deploy/furnace-backend.service deploy/furnace-gateway.service \
          deploy/furnace-update.service deploy/furnace-update.timer /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl restart furnace-backend furnace-gateway
  sudo systemctl enable --now furnace-update.timer

First-time service install (new Pi)
  create /etc/furnace-brain.env (template: deploy/furnace-brain.env.sample)
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
