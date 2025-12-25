installation
pip install fastapi uvicorn pyyaml watchfiles

dev run
./furnace-run-dev.sh 

tests
python -m pip install -U pytest
python -m pytest -q tests/test_stats_module.py

deploy
chmod +x /home/pi/furnace-brain/scripts/gateway.py
create /etc/furnace-brain.env

create /etc/systemd/system/furnace-backend.service
create /etc/systemd/system/furnace-gateway.service

sudo systemctl daemon-reload
sudo systemctl enable --now furnace-backend.service
sudo systemctl enable --now furnace-gateway.service

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
