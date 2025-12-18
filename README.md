installation
pip install fastapi uvicorn pyyaml


front
python -m http.server 5500

backend 
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000


TODO
ok- przeladowywanie ustawien po zapisie z gui
ok- gui ogien poprawa locaklizacji
- gui chowany pasek boczny
- ustawienia zaawansowane i uproszczone
ok- rozpalanie ui ustawia tryb rozpalania, przejscie do work tez powinno gdzies byc
- stop wylacza wszystko na off
- modul logow
ok- modul zaworu mieszajacego
ok- modul historii - work, temp grzejniki, temp piec, spaliny (co 30 sek)
- modul czyszczenia historii
- modul statystyk - zuzycie wegla, spalanie na godzine, 
- blokada ui
- ustawienia smart - korekcja ustawien -> za duzo sadzy, za duzo popiolu, za duza temp spalin
ok- dokladnosc wyswietlania w ustawieniach float 0.01 
ok- sortowanie modulow w ustawieniach
- problem z zamykaniem aplikacji
ok- mixer osobno czasy w iginition i work oraz czasy miedzy korektami
ok- dmuchawa cos nie respektuje temp spalin
ok- poprawki ui - dmucawa nie pokazuj procentow, slimak nie pokazuj korekty
- uproszone menu ustawien -> temp pieca, temp grzejniki, temp przelaczanie rozpalanie-work,
 dmuchawa obroty, slimak bazowe nastawy
- w schame popraw zakresy parametrow - przejdz przez wszystkie moduly
ok- mixer algorytm rozpalania jesli daleko od zadanej na grzejnikach jesli blisko to algorytm standby - nie patrzy na ignition
ok- popraw power work tak zeby pid licxzyl caly czas a uzywal dopiero w trybie work
- pompa cwu nie reaguje na stop - powinna miec takie tryby jak pompa co
- zmiana czasu
- przyspieszone testy
- modul wygaszania, tryb wygaszania pieca, 
- tryy rozpalania - jak daleko grzejniki od zadanej to najpierw zamykjaj zawor
- modul bezpieczentwa - przegrzanie slimaka, pieca, brak odczytu temperatury
