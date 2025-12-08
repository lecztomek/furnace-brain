installation
pip install fastapi uvicorn pyyaml


front
python -m http.server 5500

backend 
python -m uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000


TODO
- przeladowywanie ustawien po zapisie z gui
- gui ogien poprawa locaklizacji
- gui chowany pasek boczny
- ustawienia zaawansowane i uproszczone
- rozpalanie ustawia tryb rozpalania
- stop wylacza wszystko na off
- modul logow
- modul zaworu mieszajacego
- modul historii 
- modul statystyk - zuzycie wegla, spalanie na godzine
- blokada ui
- ustawienia smart - korekcja ustawien -> za duzo sadzy, za duzo popiolu, za duza temp spalin
- dokladnosc wyswietlania w ustawieniach float 0.01 