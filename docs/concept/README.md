# Koncept nowego ekranu głównego

Szkic wyglądu, jeszcze niepodłączony do sterownika (dane na sztywno). Otwórz `panel.html` w przeglądarce -
kliknięcie Stop / Rozpalanie / Praca przełącza widok, ☰ wysuwa menu.

- rysunek: kocioł (temperatura, trend +/-, zadana), komin ze spalinami, grzejnik (temperatura, zadana)
- lewa kolumna: dmuchawa, ślimak (z temperaturą podajnika), pompa CO, pompa CWU, zawór - stan ON/OFF;
  w STOP dmuchawa i ślimak dostają przełącznik ręczny zamiast stanu
- prawa kolumna: moc pieca (wskaźnik kołowy %, w środku kW), węgiel - dziś, teraz (kg/h) i słupki z 3 dni
  (dzisiejszy: spalone + przerywany obrys do szacunku na całą dobę)
- górny pasek: status, pogoda, data, zegar i ☰ (menu wysuwane z prawej)
- na dole duże przyciski trybów: Stop, Rozpalanie, Praca
- rysowany na siatce 1440x850 i skalowany do ekranu; czcionka Red Hat Display (na Pi - dołączyć plik czcionki)

| PRACA | STOP |
|---|---|
| ![praca](panel-praca.png) | ![stop](panel-stop.png) |
