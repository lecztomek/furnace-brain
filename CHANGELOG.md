# Zmiany

Wszystkie wersje, od najnowszej. Wersja w `VERSION`: Z = poprawka, Y = nowa funkcja, X = duża zmiana.

## 1.53.1 · 29.10.2026 11:20
- Powiadomienia: temat ntfy wylicza się z klucza podglądu zdalnego (jest tylko na Pi), więc jest znany przed pierwszym startem i nie ma go w publicznym repozytorium; bez tego klucza - losowy jak wcześniej

## 1.53.0 · 29.10.2026 11:00
- Nowy moduł „Powiadomienia na telefon” (ntfy): w Ustawieniach zaznaczasz, co wysyłać - wygaśnięcie (domyślnie włączone, najwyższy priorytet, dźwięk także w nocy), ogień słabnie mimo pełnej mocy, przegrzanie, brak czujnika, błąd modułu (domyślnie wyłączone)
- Temat ntfy generuje się sam przy pierwszym starcie (losowa nazwa, działa jak hasło) i jest widoczny w Ustawieniach → Powiadomienia na telefon - wpisz go w aplikacji ntfy (serwer ntfy.sh). „Wyślij powiadomienie testowe” - jedno powiadomienie testowe
- Wysyłane w tle, z kilkoma próbami przez ok. 20 min, gdy nie ma internetu - sterowanie nigdy nie czeka; ten sam rodzaj najwyżej raz na 5 min. Symulator (testy, środowisko testowe) nigdy nie wysyła. Gdy padnie samo Pi albo prąd, nic nie przyjdzie

## 1.52.1 · 29.10.2026 10:25
- Strona główna: zawsze najwyżej jeden znak - jest problem: czerwony ośmiokąt (zakrywa ostrzeżenie), są same ostrzeżenia: żółty trójkąt. Dotknięcie pokazuje wszystko: najpierw problemy, pod nimi ostrzeżenia
- Znak znika sam, gdy obie listy są puste - idzie za stanem sterownika (co kilka sekund): wygaśnięcie do ponownego rozpalenia, przegrzanie i brak czujnika do powrotu do normy, ostrzeżenie o węglu do powrotu ognia albo zatrzymania, brak danych do pierwszych świeżych danych

## 1.52.0 · 29.10.2026 10:10
- Strona główna: dwa różne znaki - problemy (czerwony ośmiokąt: coś stanęło albo się zepsuło - wygaśnięcie, przegrzanie, brak czujnika, błąd modułu, brak danych) i ostrzeżenia (żółty trójkąt: jeszcze działa, ale coś się szykuje). Mogą być oba naraz, obok siebie; dotknięcie pokazuje listę danego rodzaju, pasek na dole liczy jedne i drugie
- Wraca ostrzeżenie „ogień słabnie mimo pełnej mocy - sprawdź węgiel w zasypie” (żółty znak, logi, podgląd zdalny) - w chwili, gdy zaczyna się liczyć wcześniejsze zatrzymanie: 29.10 ostrzeżenie o 05:12, zatrzymanie ok. 05:22, jeśli ogień nie wróci. Gdy wróci, ostrzeżenie znika samo

## 1.51.1 · 29.10.2026 09:40
- Zamiast ostrzeżenia z 1.51.0 - wcześniejsze zatrzymanie: gdy przy pełnej mocy (co najmniej 85 % od 5 min) spaliny są poniżej 55 °C i dalej spadają, a przez kolejne 10 min nie wzrosną o 3 °C i kocioł nie rośnie, wykrywanie wygaśnięcia zatrzymuje ślimak i dmuchawę (STOP + alarm), nie czekając, aż kocioł spadnie poniżej 40 °C
- 29.10 węgiel przestał dochodzić do paleniska ok. 05:05, ślimak pchał węgiel do zgasłego paleniska do 07:13 - teraz STOP ok. 05:21. Na historii 26-29.10: zatrzymuje tylko prawdziwe wygaśnięcia, żadnego dołka, z którego ogień wrócił (28.10 18:00 i 23:20). Kocioł rosnący przy pełnej mocy (ogień się pali, a czujnik spalin kłamie) - bez zatrzymania
- Alarm i znak ostrzegawczy mówią wtedy, że węgiel nie dochodził do paleniska - sprawdź zasyp. Ustawienia: Wykrywanie wygaśnięcia → Wcześniejsze zatrzymanie; stary warunek (kocioł poniżej 40 °C) działa dalej

## 1.51.0 · 29.10.2026 08:55
- Wczesne ostrzeżenie o gasnącym ogniu (moduł wykrywania wygaśnięcia, tylko ostrzeżenie - nic nie zatrzymuje): moc co najmniej 85 % od 5 min, spaliny poniżej 55 °C i dalej spadają co najmniej 0,3 °C/min. Po zwykłym dołku przy pełnej mocy ogień wraca i spaliny zaczynają rosnąć po kilku minutach; gdy dalej spadają, do paleniska nic nie dochodzi - węgiel zawieszony w zasypie albo się skończył
- 29.10 w nocy węgiel przestał dochodzić ok. 05:05, alarm wygaśnięcia przyszedł dopiero o 07:13 (czeka na kocioł poniżej 40 °C) - ostrzeżenie byłoby o 05:12. Na historii 26-29.10 łapie wszystkie 3 prawdziwe wygaśnięcia, bez fałszywych ostrzeżeń (dołki 28.10 o 18:00 i 23:20 - ogień wrócił - bez ostrzeżenia)
- Widać je w znaku ostrzegawczym na stronie głównej („WĘGIEL”), w logach i w podglądzie zdalnym; trzyma się, aż spaliny wzrosną o 3 °C od najniższego punktu albo moc zejdzie z maksimum. Ustawienia: Wykrywanie wygaśnięcia → Wczesne ostrzeżenie

## 1.50.1 · 29.10.2026 02:05
- Krzywa minimum mocy: domyślnie 55 % przy 0 °C i +1 % na stopień mrozu (było 50 % i 1,5 %). Przy 9 °C minimum 46 % zamiast 36 % - z poprzednimi wartościami dołek z 28.10 23:20 nie zostałby złapany (PID schodził tylko do 33-42 %, a żar przerzedzał się już poniżej ok. 45 %)
- Symulator kotła: falowanie ±0,46 °C (bez minimum ±1,37), dołki najniżej 53-55 °C (bez minimum 51,4-53,7), przy mrozie -5 °C przestrzał do 60 °C (próg przegrzania 66 °C), zużycie bez zmian

## 1.50.0 · 29.10.2026 01:40
- Nowy moduł „Krzywa minimum mocy” (domyślnie włączony): jak nisko PID może zejść z mocą w pracy, ustawiane jak krzywa grzejników - minimum przy 0 °C na dworze (50 %) i wzrost na stopień mrozu (1,5 %), z temperatury uśrednionej z 3 h. Przy ok. 10 °C minimum to 35 %, przy -10 °C 65 %; granice 20-70 %
- Powód: 28.10 o 18:00 i 23:20 kocioł spadał o 4-6 °C - PID przez godzinę trzymał 25-35 % mocy przy kotle lekko ponad zadaną, żar się wypalał, a nowy węgiel rozpalał ok. 40 min
- PID nie schodzi poniżej minimum i nie zbija wtedy całki (anti-windup). Minimum działa w całości do +3 °C ponad zadaną, wyżej znika płynnie w ciągu 1 °C (od +4 °C korekta przegrzania). Bez pogody albo gdy moduł przestanie działać - tylko stała „Minimalna moc”, jak wcześniej
- Symulator kotła: falowanie ±0,8 °C zamiast ±1,37 °C, płytsze dołki po spadku poboru i po mrozie, zużycie bez zmian
- Serwis → Strojenie: przy mocy widać „min X%”, gdy krzywa minimum podtrzymuje PID; zmiany jej ustawień są na liście "Po zmianach ustawień"

## 1.49.2 · 28.10.2026 18:55
- Strojenie, "Po zmianach ustawień": zmiany zrobione w odstępie do 15 min to jedna pozycja z jednym porównaniem - "przed" kończy się na pierwszej z nich, "po" zaczyna 30 min po ostatniej. Wcześniej każda dostawała osobne porównanie i np. wyprzedzanie spalinami zmienione 2 min przed krzywą miało tylko 2 min "po", więc nigdy nie dostało oceny
- Na liście zmian także uśrednianie krzywej (curve_avg_h) - zmienia zadaną grzejników

## 1.49.1 · 28.10.2026 18:30
- Trend pod temperaturą kotła bez strzałek - sam znak i kolor: „+1,2°/10 min” (pomarańczowy), „−1,5°/10 min” (niebieski)

## 1.49.0 · 28.10.2026 18:20
- Strona główna: pod temperaturą kotła trend - o ile stopni na 10 min rośnie (▲, pomarańczowy) albo spada (▼, niebieski), np. „▼ −1,5°/10 min”; widać też w podglądzie zdalnym
- Nowy moduł „Trend temperatury” (pomocniczy, nie steruje niczym): odczyt kotła co 30 s, trend z ostatnich 10 min liczony z dopasowania prostej, więc skoki czujnika o 0,06 °C nie udają trendu; bez odczytów (start, zgubiony czujnik) pokazuje „trend --”

## 1.48.2 · 28.10.2026 17:10
- Symulator: Strojenie → "Po zmianach ustawień" działa także w symulatorze. Zmiana ustawień, historia ustawień i Strojenie liczą czas zegarem pieca, tak jak historia temperatur; w symulatorze ten zegar biegnie szybciej niż prawdziwy, więc wcześniej porównanie nie miało danych „po” albo brało złe okresy
- Na Pi bez zmian: tam zegar pieca to zwykły zegar

## 1.48.1 · 28.10.2026 16:25
- Zmiany (Serwis → Zmiany) po polsku - cały changelog przetłumaczony, nowe wpisy też po polsku
- Serwis → Moduły: opisy modułów po polsku

## 1.48.0 · 28.10.2026 16:18
- Strojenie, "Po zmianach ustawień": pełniejsze porównanie przed / po - fala, średnie odchylenie od zadanej, czas w ±2 °C, wahania i skoki mocy, średnia moc, moc na stopień mrozu (węgiel z odjętym wpływem pogody), temperatura spalin i na zewnątrz, każde z oceną lepiej / gorzej
- Po = od 30 min po zmianie do następnej zmiany albo do teraz, maksymalnie 3 dni; przed = tyle samo, ale nie wcześniej niż poprzednia zmiana. Uwagi przy małej ilości danych i innej pogodzie; widać wersję aplikacji działającą w chwili zmiany (aktualizacje nie dzielą porównania)

## 1.47.1 · 28.10.2026 15:55
- Testy: udawane lokalne API podglądu zdalnego zna /api/service/modules (1.47.0 trafiła z 4 nieprzechodzącymi testami - brakowało tego tylko w atrapie testowej)

## 1.47.0 · 28.10.2026 15:47
- Serwis → Moduły: każdy moduł z opisem, za co odpowiada (po angielsku, z modules.yaml), czy jest kluczowy (pętla sterowania) czy pomocniczy, jego stan i ostatni błąd z godziną i liczbą wystąpień (zostaje po tym, jak moduł wróci do działania); dotknięcie wiersza pokazuje ostatni wpis w logu, ostatnie ostrzeżenie / alarm i czas kroku. Także w podglądzie zdalnym
- Jądro i pętla pomocnicza pamiętają ostatni błąd modułu po jego naprawie; magazyn stanu trzyma ostatnie zwykłe i ostatnie problemowe zdarzenie każdego źródła

## 1.46.1 · 28.10.2026 15:41
- Moduł bezpieczeństwa nie zagląda już do modułu przegrzania: nie blokuje zaworu mieszającego, gdy w ogóle nie ma odczytu temperatury grzejników - zawór i tak nie reguluje bez własnego czujnika (stoi), a otwieranie przy przegrzaniu, tryb ręczny i zamykanie zaworu przy zimnym kotle (żadne z nich nie potrzebuje temperatury grzejników) nadal działają

## 1.46.0 · 28.10.2026 15:38
- Nowy moduł "Krzywa grzewcza" (curve): wyznacza zadaną dla grzejników (z krzywej według uśrednionej temperatury zewnętrznej albo stałą) - wydzielony z zaworu mieszającego, który teraz tylko dąży do tej zadanej. Ustawienia zadanej i krzywej przechodzą do Ustawienia → Krzywa grzewcza; zapisane wartości są przejmowane jednorazowo (i zostają w pliku zaworu na wypadek cofnięcia wersji)
- Temperatura zewnętrzna jest czytana ze wspólnego stanu, do którego wpisuje ją moduł pogody (backend/core/outside.py) - jądro nie kopiuje już pogody do czujników
- Sprawdzone: 6 h symulatora (rozpalanie, praca, pogoda +5 → -5 °C, krzywa włączona) daje identyczne temperatury kotła i grzejników oraz te same 243 ruchy zaworu przed i po zmianie

## 1.45.1 · 28.10.2026 15:28
- Porządki w kodzie (bez zmian w działaniu pieca): okablowanie wyniesione z main.py - warstwa sprzętowa czyta je przy starcie (backend/hw/wiring.py), należy ono do modułu Sprzęt (sprawdza zapis, przekazuje zmianę do sprzętu); main.py tylko składa części razem

## 1.45.0 · 28.10.2026 15:18
- Nowy moduł "Historia ustawień" (settings_log): każde ustawienie każdego modułu z wersją aplikacji i commitem zapisywane na pendrive (settings_YYYYMM.jsonl) - przy starcie (każda aktualizacja), przy zmianie pliku ustawień (w GUI albo ręcznie) i raz dziennie; bez sekretów
- Serwis → Strojenie, "Po zmianach ustawień": zmiany także z historii ustawień (to, czego nie złapał dziennik zdarzeń) i aktualizacje wersji aplikacji, każda porównana 12 h przed / po

## 1.44.6 · 28.10.2026 14:24
- Przegrzanie kotła otwiera zawór mieszający nawet wtedy, gdy czujnik grzejników nie ma odczytu (wcześniej moduł bezpieczeństwa blokował wtedy zawór, więc ciepło nie miało jak odejść do domu); poza przegrzaniem brak odczytu grzejników nadal zatrzymuje zawór

## 1.44.5 · 28.10.2026 14:04
- Ochrona przed przegrzaniem: kocioł (66 °C) i zasobnik (cofnięcie ognia, 60 °C) są pilnowane osobno - wcześniej brak odczytu zasobnika (od czasu do czasu znika) wyłączał też ochronę kotła przed przegrzaniem, i na odwrót
- Bez odczytu ochrona zachowuje swój stan: alarm kończy się dopiero, gdy czujnik wróci i wystygnie; ostrzeżenie podaje, którego czujnika brakuje

## 1.44.4 · 28.10.2026 13:51
- Ustawienia → Sprzęt: bez listy z magistrali (podgląd zdalny) zapisane czujniki nie pokazują się już jako "nie ma na przewodzie" - tylko numer; podgląd zdalny też dostaje listę (numer i temperatura)

## 1.44.3 · 28.10.2026 13:47
- Szacunek ciepłej wody według zasilania grzejników, a nie temperatury zewnętrznej: w ciepłe południe grzejniki dostają tyle samo co w nocy (krzywa idzie za wielogodzinną średnią, dom wolno stygnie), więc stary model liczył całe południe jako ciepłą wodę (prod 28.10: 10.7 kWh do 14:35, teraz 2.1 kWh). Ostatnie 30 dni jest przeliczane jednorazowo

## 1.44.2 · 28.10.2026 13:27
- Ustawienia → Sprzęt odwrotnie: każde fizyczne wyjście (GPIO16, 20, 21, 6, 12) dostaje funkcję z listy, każde z 6 miejsc na czujnik dostaje czujnik wybrany spośród znalezionych na magistrali (numer i temperatura) i rolę. Zamiana pomp = GPIO20 → pompa CO, GPIO21 → pompa CWU, jeden zapis
- Funkcja na dwóch wyjściach albo na żadnym, czujnik lub rola dwa razy, brak czujnika kotła - odrzucane z podaniem powodu; domyślnie nadal jest pierwotne okablowanie

## 1.44.1 · 28.10.2026 13:21
- Ustawienia → Sprzęt: dmuchawy nie ma już na liście GPIO - to nie przekaźnik, jej obroty to PWM na GPIO19 do YYAC-3S (na stałe; wyjścia przekaźnikowe nie mogą używać GPIO19). "Odwrócone PWM" zostaje

## 1.44.0 · 28.10.2026 13:14
- Ustawienia → Sprzęt: które GPIO steruje podajnikiem, pompami CO i CWU, zaworem i wentylatorem, i który DS18B20 to kocioł, zasobnik, grzejniki, zbiornik CWU, powrót, za zaworem - wcześniej wpisane na sztywno w main.py. Domyślnie stare okablowanie, więc aktualizacja niczego nie zmienia
- Zapis z jednym GPIO dwa razy, z niebezpiecznym pinem (I2C, 1-Wire, SPI, port szeregowy) albo z jednym czujnikiem w dwóch miejscach jest odrzucany z podaniem powodu; uszkodzony plik oznacza powrót do starego okablowania (widać to w Diagnostyka Pi)
- Zmiana działa od razu i bezpiecznie: wszystkie wyjścia na LOW, potem następny krok sterowania włącza to, co ma być włączone (pin PWM wentylatora po restarcie)
- Serwis → Diagnostyka Pi → Czujniki 1-Wire: każdy czujnik na magistrali z temperaturą i rolą, nieprzypisane oraz przypisane numery, których nie ma na magistrali

## 1.43.0 · 28.10.2026 12:57
- Strojenie: przy łagodnej, regularnej fali najpierw podpowiada wyprzedzanie spalinami ("Wyprzedzanie spalinami" 0 → 5, potem do 8); łagodniejsze Kp / Ki dopiero, gdy wyprzedzanie jest na 8
- Strojenie: po zmianie ustawień PID (Kp, Ki, wyprzedzanie spalinami, limity mocy, dawka podajnika) liczy się tylko praca od tej chwili - żadnych podpowiedzi ze starej fali, dopóki nie minie 3 h ustabilizowanej pracy
- Strojenie: PID oceniany z ostatnich 12 h (było 24 h); wyprzedzanie spalinami widać w "Po zmianach ustawień"

## 1.42.1 · 28.10.2026 12:51
- Hamulec spalinowy nazywa się w ustawieniach "Wyprzedzanie spalinami" - działa w obie strony: mniej mocy, gdy spaliny rosną, więcej, gdy spadają

## 1.42.0 · 28.10.2026 12:46
- Moc w PRACY: opcjonalny hamulec spalinowy (Ustawienia → Moc w pracy → "Hamulec spalinowy", 0 = wyłączony, domyślnie). Spaliny nagrzewają się 10-20 min przed wodą w kotle, więc moc -= wzmocnienie x trend spalin [°C/min] (wygładzony z 5 min, maksymalnie "Hamulec spalinowy - maks." punktów, błędny odczyt pomijany, bez czujnika nic, liczone od nowa po wejściu w PRACĘ)
- Sprawdzone na bliźniaku kotła dopasowanym do prod (fala ±1.4 °C co ~80 min, spaliny 14 min wcześniej): wzmocnienie 8 zmniejsza falę do ±0.5-1.0 °C, tak samo szybki powrót po kąpieli, szybciej po zmianie zadanej lub obciążenia

## 1.41.4 · 28.10.2026 12:36
- Podgląd zdalny: statystyki nie zawieszają się już na "liczę aktualne" - wysyłany raport to ostatni ukończony, bez znacznika "jeszcze liczę"

## 1.41.3 · 28.10.2026 12:27
- Strojenie: bez podpowiedzi o średniej zewnętrznej krzywej (ustawienie jednorazowe, nie warte osobnego wiersza)

## 1.41.2 · 28.10.2026 12:24
- "W domu za ciepło albo za zimno" to sekcja karty "Co zmienić", a nie osobna karta

## 1.41.1 · 28.10.2026 12:23
- "W domu za ciepło albo za zimno": według temperatury zewnętrznej, dla której ustawia się krzywą - za zimno / za ciepło przy 0 °C (curve_at_0), przy -10 °C i przy +10 °C (curve_slope), z temperaturami grzejników przed i po
- Tabele serwisowe na telefonie: nazwa i wartość u góry, tekst pod spodem - nic nie jest ucięte z prawej

## 1.41.0 · 28.10.2026 12:22
- Strojenie: łagodna, ale regularna fala kotła (±1-1.5 °C, co 40+ min) dostaje podpowiedź, by złagodzić PID (Kp i Ki -20%) - na żółto, nie liczona jako problem
- Strojenie: wiersze krzywej grzewczej - grzejniki przy +5 / 0 / -10 / -15 °C i moc, jakiej wymagałoby to w mróz (z grubsza, z dzisiejszej pracy), podpowiedź, gdy średnia zewnętrzna jest krótsza niż 12 h
- Strojenie: nowa karta "W domu za ciepło albo za zimno" - które ustawienie krzywej zmienić i na ile, z obecnymi wartościami

## 1.40.2 · 28.10.2026 11:44
- Prognozy węgla (jutro, sezon) i sprawność biorą tylko dni, w których kocioł palił się przez większość doby - dzień, w którym stał wyłączony przez połowę czasu, zaniżał każdą prognozę mniej więcej o połowę

## 1.40.1 · 28.10.2026 11:18
- Ciepła woda: godzinowe sumy trzymane w pamięci dni są zaokrąglane (~0.7 KB na dzień zamiast ~2 KB)

## 1.40.0 · 28.10.2026 11:16
- Statystyki, nowa zakładka Woda: ile węgla idzie na ciepłą wodę, a ile na grzejniki (szacunek - noc 0-5 to samo ogrzewanie, moc ponad tę linię przy tej samej temperaturze zewnętrznej to ciepła woda), dziennie i według godziny doby, z ikonami wody
- Dzień, który o północy zaczyna się w PRACY, liczy się jako ustabilizowany od pierwszego wiersza (pracuje od wczoraj)

## 1.39.5 · 28.10.2026 10:45
- Przerwy STOP wracają jako szare pasy na całą wysokość wykresu (za liniami, jaśniejsze), tabela ostatnich postojów zostaje

## 1.39.4 · 28.10.2026 10:44
- Podgląd zdalny: skrypty i style mają wersję w adresie - po wgraniu strona nie miesza już nowej strony ze starym skryptem z pamięci podręcznej ("Cannot set properties of null")

## 1.39.3 · 28.10.2026 10:31
- Przerwy STOP nie są już rysowane na wykresach: pasek pod każdym wykresem 5 min (pod godzinami) i tabela ostatnich postojów (od, do, jak długo)

## 1.39.2 · 28.10.2026 10:27
- Pasy STOP: przerwa to STOP razem z rozpalaniem zaraz po nim (2-minutowy STOP był ledwie kreską), szerokość co najmniej 10 px ze znacznikiem u góry

## 1.39.1 · 28.10.2026 10:05
- Strojenie: 2 h po zmianie zadanej (≥1 °C) są pomijane - gorący kocioł przestrzeliwujący niższą zadaną nie jest już brany za wahania PID
- Strojenie: wahania zgłaszane od ±1.5 °C (było ±1.0), "źle" od ±2.5 °C - złoże węgla reaguje wolno, ±1-1.5 °C to norma

## 1.39.0 · 28.10.2026 09:59
- Statystyki, Praca: każdy STOP (dosypanie węgla, wybranie popiołu) jako szary pas na 5-minutowych wykresach mocy i temperatury, z listą postojów (godzina i minuty) pod wykresem temperatury
- Strojenie: wiersz dmuchawy wyjaśnia, jak oceniać płomień i popiół (dobrze / za mało powietrza / za dużo powietrza)

## 1.38.3 · 28.10.2026 10:30
- Zmiany: każda wersja pokazuje swój tag albo commit - co wpisać w "Deploy to prod", żeby do niej wrócić

## 1.38.2 · 28.10.2026 10:25
- Zmiany jako oś czasu: wersje pogrupowane według dni z godziną, u góry pasek dni (ile wersji każdego dnia)

## 1.38.1 · 28.10.2026 10:23
- Zmiany pokazują każdą wersję (także każdą poprawkę), z datą

## 1.38.0 · 28.10.2026 10:19
- Serwis: zakładka 'Zmiany' z listą zmian

## 1.37.9 · 28.10.2026 10:13
- Diagnostyka Pi: wszystkie ostrzeżenia pod 'Do sprawdzenia'; bez fałszywego ostrzeżenia o logach
- Brak miejsca na dyskach / w RAM był tylko kolorowym paskiem - teraz jest też na liście, więc plakietka 'są uwagi' zawsze ma pod sobą powód
- Dziennik zdarzeń jest kompaktowany i zapisywany tylko, gdy coś się dzieje: alarmuje po 30 min bez zapisu, a nie po 3

## 1.37.8 · 28.10.2026 10:09
- Strona główna: średnia zewnętrzna w plakietce temperatury, tak jak zadana grzejników

## 1.37.7 · 28.10.2026 10:08
- Diagnostyka Pi: szczegóły zawsze pod nagłówkiem

## 1.37.6 · 28.10.2026 10:03
- Strona główna: średnia zewnętrzna i jej okno pod temperaturą zewnętrzną

## 1.37.5 · 28.10.2026 10:02
- Statystyki nie zawieszają się już na przeliczaniu; tabele serwisowe; wykresy 5 min
- Dni przeliczone na nowo dla nowych pól mocy nigdy nie były zapisywane (rozmiar pamięci dni się nie zmieniał), więc każde odświeżenie czytało 30 dni plików od nowa i strona stała na 'liczę aktualne'; teraz każdy dzień przeczytany z plików liczy się jako niezapisany. Nieudane przeliczenie trafia do logu, zamiast przepaść.
- Serwis: 'Co zmienić' i problemy w Kontrola / Diagnostyka jako tabele pod nagłówkiem 'Do sprawdzenia' zamiast kart
- Wykresy regulacji i mocy z ostatniej doby w krokach 5 min

## 1.37.4 · 28.10.2026 09:38
- Diagnostyka Pi: po jednym pasku dla CPU, RAM, karty SD i pendrive'a

## 1.37.3 · 28.10.2026 09:30
- Strona serwisowa: pierścienie i paski stref zamiast zwykłych kafelków
- Strojenie: pierścień z udziałem czasu w granicach 2 C od zadanej i paski ze strefami dobrze / średnio / źle oraz znacznikiem dla odchylenia, wahań i średniej mocy
- Diagnostyka Pi: pierścienie dla temperatury CPU, RAM i dysku; kafelki stanu z ikonami dla zasilania, internetu, czujników (po kropce na każdy) i czasu działania

## 1.37.2 · 28.10.2026 09:26
- Porady Strojenia przeliczane co 10 min zamiast co 30

## 1.37.1 · 28.10.2026 09:20
- Uzupełnianie historii po restarcie: tylko potrzebne pliki godzinowe, w tle

## 1.37.0 · 28.10.2026 09:07
- Statystyki: karta 'Moc pieca' - kWh, moc %, stabilność
- Kafelki: średnie i szczytowe kW, średnia moc regulatora z rozrzutem, udział czasu na pełnej mocy (>=90%) i na minimum (<=25%)
- Wykresy: ciepło na godzinę (ostatnia doba) lub na dzień w kWh; moc regulatora w % co 10 min (ostatnia doba) lub średnia dzienna w PRACY
- Dziennie: sumy mocy w ustabilizowanej PRACY; ostatnie 30 dni z pamięci jest jednorazowo przeliczane w tle, żeby je uzyskać

## 1.36.1 · 28.10.2026 08:57
- Krzywa grzewcza: domyślnie średnia z 12 h; awaria pogody zachowuje średnią

## 1.36.0 · 28.10.2026 08:56
- Odporność na restart: wyjścia wyłączone na czas przestoju, okna uzupełniane z historii
- Przy wyłączaniu ślimak, dmuchawa i zawór mieszający stają (piny trzymają stan do startu nowego procesu); przy kotle powyżej 45 C pompy pracują
- Wygaśnięcie: okno 30 min jest uzupełniane z pliku CSV historii, więc ochrona działa od razu po restarcie (tylko nieprzerwany odcinek PRACY)
- Krzywa grzewcza: średnia zewnętrzna (6 h) jest uzupełniana z historii, zamiast zaczynać od bieżącego odczytu

## 1.35.6 · 28.10.2026 08:39
- Kontrola z ostatnich 3 dni; każda karta podaje swój okres
- Sprawdzenia używają nowego okresu 3 dni (z dzisiejszym); kafelki liczone z innego okresu (trend sprawności, zużycie a pogoda) to zaznaczają
- Karty statystyk pokazują 'ostatnie 7 dni (od 22.10)' itp. obok tytułu, karta sezonu jego początek; karty serwisowe mówią, z czego są liczone

## 1.35.5 · 28.10.2026 08:34
- Wykres regulacji z ostatniej doby w krokach 10 min

## 1.35.4 · 28.10.2026 08:30
- Wykres regulacji pokazuje najniższą temperaturę każdej godziny; okresy podane na kafelkach

## 1.35.3 · 27.10.2026 21:14
- Kontrola jako małe kafelki z jedną liczbą każdy, problemy wyjaśnione poniżej

## 1.35.2 · 27.10.2026 21:00
- Podgląd zdalny: strona serwisowa i prognoza; README napisane od nowa, dodany CHANGELOG

## 1.35.1 · 27.10.2026 20:47
- Kontrola przeniesiona ze statystyk na stronę serwisową

## 1.35.0 · 27.10.2026 20:40
- Statystyki w stylu strony serwisowej; nowoczesne ikony liniowe zamiast emoji
- Statystyki: duże kafelki z ikonami, krótkie teksty, sprawdzenia jako karty (szczegóły po dotknięciu, problemy otwarte), model pogodowy jako trzy kafelki, większe opisy wykresów, nic poniżej 15 px
- Js/icons.js: ikony liniowe w stylu Lucide w kolorowych plakietkach na obu stronach, ikony pogody barwione według nieba

## 1.34.0 · 27.10.2026 20:26
- Strona serwisowa od nowa: duże kafelki z ikonami i karty, krótkie teksty, bez drobnego druku
- Strojenie: trzy duże kafelki, PID teraz jako cztery pola (pasek mocy podzielony na P i I), jedna karta na obszar ze zmianą dużymi cyframi; dotknięcie karty pokazuje opis; pozostałe obszary tylko, gdy coś jest nie tak
- Zmiany ustawień jako paski przed / po, ukryte, gdy ich nie ma
- Diagnostyka: sześć dużych kafelków, problemy jako karty, wszystkie szczegóły zwinięte
- Nic mniejszego niż 15 px

## 1.33.4 · 27.10.2026 19:41
- Strona serwisowa: menu (hamburger) działa (brakowało opakowania aplikacji)

## 1.33.3 · 27.10.2026 19:38
- Diagnostyka: kiedy uruchomiła się każda analiza w tle i ile trwała

## 1.33.2 · 27.10.2026 19:26
- Strona serwisowa i prognoza odświeżane rzadziej
- Porady Strojenia przeliczane najwyżej co 30 min, nic nie jest czytane, zanim strona zostanie otwarta
- Strona serwisowa odświeża się co 30 s, polecenia systemowe co 5 min
- Prognoza pogody pobierana co 3 h

## 1.33.1 · 27.10.2026 19:23
- Strona serwisowa nigdy nie czeka na analizę; minione dni czytane raz
- Porady Strojenia liczone w wątku w tle (pierwsze z przerwami, rozgrzewka 2 min po starcie); zapytanie wraca od razu, strona pokazuje postęp
- Minione dni historii / zdarzeń trzymane w pamięci, przeliczenie czyta tylko dzisiejszy
- Katalogi danych listowane raz na przeliczenie zamiast glob na każdy dzień
- Diagnostyka: polecenia systemowe zapamiętywane na minutę, najnowszy plik danych znajdowany bez stat każdego pliku

## 1.33.0 · 27.10.2026 17:50
- Strona serwisowa zamiast Historii: porady strojenia PID i diagnostyka Raspberry Pi; zakładka prognozy w statystykach
- Serwis / Strojenie: całka (Ki), Kp, dawka podajnika i dmuchawa oceniane z ostatnich 24 h ustabilizowanej pracy (od 2 h po rozpaleniu), z konkretnymi zmianami; łańcuch PID na żywo (uchyb, P, I, moc, rytm podajnika, dmuchawa); zmiany ustawień porównywane 12 h przed / po
- Każda zmiana ustawień jest logowana (SETTINGS_CHANGED, stara -> nowa)
- Serwis / Diagnostyka Pi: temperatura CPU, zasilanie (dławienie), obciążenie, pamięć, dyski, zapis danych, czasy pętli, internet, czujniki, moduły
- Statystyki / Prognoza: prognoza Open-Meteo na 3 dni (pobierana co godzinę) z węglem, kosztem i ciepłem oczekiwanymi na podstawie dni, w których piec pracował

## 1.32.2 · 27.10.2026 17:13
- Statystyki: kg na lewej osi, zł na prawej, na tych samych liniach siatki

## 1.32.1 · 27.10.2026 17:10
- Symulator: zbiornik CWU 140 l, jak prawdziwy

## 1.32.0 · 27.10.2026 17:04
- Statystyki: prostsze i większe; zakresy osobno dla każdej karty, tylko tam, gdzie mają sens
- Każda karta/zakładka ma własny zakres (Dziś = ostatnie 24 h / 7 dni / 30 dni / Sezon) i wszystko pod nią za nim idzie; wykres sezonu, pogoda i sprawdzenia nie mają zakresu. Usunięte 'Wczoraj'.
- Duże liczby na górze (węgiel, koszt, na dzień; 'w normie' % czasu).
- Wykresy: każdy pokazuje jedną rzecz, z tytułem słownym, grube linie bez kropek, większe opisy, jedna oś. Węgiel: same słupki (oś kg/zł; dymek kg, zł, kWh, temperatura zewnętrzna). Regulacja: linia kotła na zielonym pasie (zadana +-2 C) z zapasem wokół. Sprawność: ukryta z dopiskiem, dopóki nie ma dni z temperaturą zewnętrzną. Spadki: ostatnie 5.

## 1.31.1 · 27.10.2026 16:56
- Strona główna: znak ostrzegawczy to samo '!', dotknięcie otwiera listę problemów

## 1.31.0 · 27.10.2026 16:56
- Moduł wygaśnięcia; znak ostrzegawczy na stronie głównej przy każdym problemie

## 1.30.4 · 27.10.2026 16:46
- Statystyki: koszt w zł na wykresach węgla

## 1.30.3 · 27.10.2026 16:43
- Statystyki: węgiel na godzinę tylko dla dziś i wczoraj, czytany na bieżąco

## 1.30.2 · 27.10.2026 16:39
- Statystyki: wykresy idą za wybranym okresem; energia w kWh zamiast % mocy

## 1.30.1 · 27.10.2026 16:25
- Statystyki: zapytania nigdy nie czekają na przeliczenie raportu; widać postęp przeliczania; cena węgla 1200 zł/t

## 1.30.0 · 27.10.2026 16:17
- Statystyki: zakładki, koszty, prognoza sezonu i porównanie z poprzednim sezonem; mniej mylących liczb
- Zakładki u góry: Zużycie / Praca / Sprawność / Kontrola (zapamiętywane).
- Ustawienie ceny węgla (Statystyki, zł/t): koszt okresu, na dzień, sezonu do tej pory i prognoza.
- Sezon: zużycie do tej pory a ta sama część poprzedniego sezonu, prognoza na cały sezon (węgiel na stopniodzień x typowa pogoda w Nowym Sączu do końca maja) i miesięczny wykres tego sezonu obok poprzedniego. Raport czyta teraz także dni z poprzedniego sezonu.
- Usunięte: moc szczytowa (5-minutowe serie podajnika), energia w węglu, strata kominowa i temperatura spalin (czujnik spalin pokazuje o wiele za nisko), % wentylatora / podajnika. 'Jak trzyma temperaturę' skrócone do 3 kafelków.
- Sprawdzenia: rozpalenia liczą tylko ROZPALANIE trwające 5+ min (nie kliknięcia), czujnik kotła jest zły dopiero powyżej 5% brakujących odczytów, bez sprawdzania spalin.

## 1.29.1 · 27.10.2026 16:01
- PID: zmiana ki nie powoduje skoku mocy

## 1.29.0 · 27.10.2026 15:53
- Symulator dopasowany do prawdziwego kotła; ustawienia staging = prod; wszystkie testy przechodzą
- Węgiel dociera do ognia 6 min po podaniu ślimakiem (retorta) i spala się przez ~15 min; bezwładność kotła 240 kg ekwiwalentu wody (w rzeczywistości temperatura rośnie jeszcze ~30 min po spadku mocy, rozpalanie 33->55 C w ~1 h);
- Zapotrzebowanie domu ~4.3 kW przy 0 C / połowa przy +10 C, sprawność ~75%, małe straty kotła/CWU, podajnik 12 kg/h, niespalony węgiel tylko ~5% przy wyłączonym wentylatorze (było ~70% przy wentylatorze 28%, więc kocioł potrzebował 90% mocy);
- Z ustawieniami z poprzedniego sezonu odtwarza prawdziwy kocioł: 1.5 C poniżej zadanej, wahania 0.57 C, 56% mocy przy 0 C (w rzeczywistości 1.1-1.8 / 0.6 / 53%).

## 1.28.3 · 27.10.2026 15:08
- Zmiana krzywej logowana z nową zadaną; statystyki: zawsze ostatnie 24 h, moc w kW; poprawione testy zaworu mieszającego
- Zawór mieszający: zapis nowych ustawień krzywej grzewczej loguje MIXER_CURVE_CHANGED z zadaną, jaką daje w tej chwili.
- Statystyki: 'Regulacja w ciągu doby' zawsze pokazuje ostatnie 24 godziny (strona otwiera się na 7 dniach, co pokazywało średnią 0-23); przyciski okresu zmieniają tabelę spadków i uwagę o najgorszej godzinie. 'Praca pieca' pokazuje średnią moc w kW (% regulatora w wierszu jednostki).
- Test_mixer_module: przesuwa zegar monotoniczny, według którego moduł odmierza impulsy, i używa własnego katalogu ustawień.

## 1.28.2 · 27.10.2026 14:40
- Mniejsze zużycie karty SD: bez wpisu w logu przy każdym zapytaniu HTTP

## 1.28.1 · 27.10.2026 14:20
- Statystyki: wykres regulacji pokazuje ostatnie 24 godziny dla 'dziś'

## 1.28.0 · 27.10.2026 14:09
- PID: klasyczna całka nie narasta, gdy kocioł dochodzi do zadanej

## 1.27.4 · 27.10.2026 13:19
- Klasyczna całka PID znów domyślnie włączona

## 1.27.3 · 27.10.2026 13:13
- Podgląd zdalny: wpisy w logu z tym samym czasem nie nadpisują się już nawzajem

## 1.27.2 · 27.10.2026 12:55
- Klasyczna całka PID domyślnie wyłączona

## 1.27.1 · 27.10.2026 12:53
- Podgląd zdalny: co 15 min podsumowanie w logu tego, co zostało wysłane

## 1.27.0 · 27.10.2026 12:02
- PID trzyma zadaną: klasyczna całka z ochroną przed nasyceniem (anti-windup)

## 1.24.0 · 27.10.2026 10:44
- Usunięte regulatory mocy rozmyty i predykcyjny

## 1.23.6 · 27.10.2026 09:25
- Strona główna: kropki stanu pomp były zamienione

## 1.23.5 · 27.10.2026 09:22
- Pompa CO: ustawienia zmienione w GUI działają od razu; jej tryb działa

## 1.23.4 · 27.10.2026 09:14
- Strona główna: temperatura grzejników na grzejniku, z jej zadaną

## 1.23.3 · 27.10.2026 08:55
- Telefon: przycisk menu w jednym miejscu na każdej stronie; historia się przewija
- Przycisk menu jest w prawym dolnym rogu na każdej stronie na telefonie (także główna, ręczna, statystyki - wcześniej był tam po lewej)
- Historia: strona się przewija, więc da się dojść do wszystkich przycisków serii pod wykresem; zwarte przyciski serii, stała wysokość wykresu
- Tryb ręczny: długie nazwy się zawijają, zamiast wypychać przełączniki poza ekran

## 1.23.2 · 27.10.2026 08:53
- Statystyki widać od razu po restarcie
- Ostatni raport jest zapisywany do analytics_report.json; po restarcie strona od razu go pokazuje ("Stan na HH:MM • liczę aktualne…"), a w tle liczy się nowy, i sama się przeładowuje, gdy jest gotowy
- Pamięć dni jest zapisywana co 10 nowo policzonych dni, więc restart w środku długiego pierwszego przeliczania (miesiące CSV na Pi) nie wyrzuca już pracy - przy wielu wgraniach z rzędu za każdym razem zaczynało się od zera
- Przeliczanie w tle startuje 5 s po starcie zamiast 20 s

## 1.23.1 · 26.10.2026 19:46
- Stabilne kW / kg/h na stronie głównej: mierzone z pełnych cykli podajnika

## 1.23.0 · 26.10.2026 19:44
- Strona główna: bieżące zużycie węgla w kg/h pod mocą cieplną

## 1.22.4 · 26.10.2026 19:04
- Ustawienia na panelu 800x480: moduły w jednym przewijanym rzędzie

## 1.22.3 · 26.10.2026 19:01
- Ustawienia da się obsługiwać na telefonie

## 1.22.2 · 26.10.2026 18:59
- Logi i historia czytelne na telefonie
- Logi (ekrany do 640 px szerokości): każdy wpis jako karta - czas, źródło i poziom w pierwszej linii, wiadomość (2 linie) pod spodem; filtry w dwóch kolumnach; bez wiersza nagłówków kolumn
- Historia: zwarty pasek zakresu (jeden rząd przycisków, porównanie i odświeżanie pod nim), wyższy wykres
- Obie: przycisk menu przechodzi do prawego dolnego rogu, żeby nie zasłaniał listy / wykresu
- Czasy w logach wszędzie w krótkim polskim formacie ("26.10, 18:00:04")

## 1.22.1 · 26.10.2026 18:56
- Wycofane powiadomienia na telefon (ntfy) - przełożone na później

## 1.22.0 · 26.10.2026 18:55
- Powiadomienia na telefon przez ntfy - zimne spaliny przy dużej mocy

## 1.21.3 · 26.10.2026 18:53
- Ikona strony: płomień w karcie przeglądarki i na ekranie głównym telefonu/tabletu

## 1.21.2 · 26.10.2026 17:12
- Znak ostrzegawczy braku danych: tylko na stronie głównej, w lewym dolnym rogu

## 1.21.1 · 26.10.2026 17:10
- Ostrzeżenie o braku danych jako duży trójkątny znak drogowy nad zbiornikiem

## 1.21.0 · 26.10.2026 17:08
- Statystyki: regulacja w ciągu doby; ostrzeżenie, gdy przestają płynąć dane
- Strona statystyk, karta "Regulacja w ciągu doby": dla każdej godziny doby średnia temperatura kotła a zadana, udział czasu ponad 4 C poniżej niej i średnia moc, do tego lista spadków (kiedy, jak długo, najniższa temperatura, moc przed i maksymalna) - wieczorne spadki przy kąpieli widać od razu
- Każda strona (także podgląd zdalny): "⚠ Brak nowych danych od HH:MM (N min)" u góry, gdy znacznik czasu pętli sterowania nie zmienia się przez 30 s (90 s w podglądzie zdalnym) albo backend nie odpowiada; dane starsze niż 5 min są oznaczane od razu przy ładowaniu strony

## 1.20.0 · 26.10.2026 16:52
- Błędy na stronie logów mówią, co nie zadziałało i dlaczego
- Każdy nieoczekiwany błąd API (HTTP 500) jest logowany z zapytaniem, błędem i ostatnimi liniami śladu (traceback) (źródło "api"), najwyżej co 10 min dla tego samego błędu; odpowiedź zawiera treść błędu
- Podgląd zdalny: każda część (stan, historia i logi, statystyki, ustawienia, historia 5 min, sprzątanie) jest wysyłana i raportowana osobno - niedziałający endpoint jest podawany ze statusem HTTP i odpowiedzią serwera, a reszta i tak wychodzi
- Podgląd zdalny i pogoda: problem jest logowany, gdy się zaczyna lub zmienia, powtarzany co 30 min, dopóki trwa (z liczbą prób), i logowany ponownie, gdy zostanie naprawiony; DNS, przekroczenia czasu i odrzucone połączenia opisane prostymi słowami

## 1.19.3 · 26.10.2026 16:41
- Strona główna: kropki pomp z powrotem obok pomp, tylko niżej

## 1.19.2 · 26.10.2026 16:39
- Strona główna: kropki stanu pomp pod pompami

## 1.19.1 · 26.10.2026 16:35
- Strona główna: moc cieplna obok procentu mocy

## 1.19.0 · 26.10.2026 16:28
- Strona główna: bieżąca moc cieplna w kW

## 1.18.1 · 26.10.2026 15:33
- GUI samo się przeładowuje po aktualizacji

## 1.18.0 · 26.10.2026 15:21
- Strona główna: przełącznik "DMUCHAWA RĘCZNIE" obok dmuchawy

## 1.17.0 · 26.10.2026 15:13
- Tryb kotła przetrwa restarty - aktualizacje i zaniki prądu

## 1.16.4 · 26.10.2026 14:52
- Logi: wyszukiwanie z filtrem czyta tylko dwa ostatnie pliki godzinowe

## 1.16.3 · 26.10.2026 14:51
- Logi: szybkie wyszukiwanie według źródła na Pi
- Przy rzadko logującym module strona czekała minutami. Pliki, które nie zawierają szukanej wartości, są teraz pomijane bez parsowania, a wyszukiwanie z filtrem kończy się po 10 s i zwraca to, co znalazło

## 1.16.2 · 26.10.2026 14:38
- Statystyki: czytanie uszkodzonych plików CSV; menu na wąskich ekranach

## 1.16.1 · 26.10.2026 14:30
- Poprawka: backend przestawał odpowiadać na Raspberry Pi

## 1.16.0 · 26.10.2026 13:22
- Podgląd zdalny na GitHub Pages + Firebase zamiast Render

## 1.15.0 · 26.10.2026 13:09
- Statystyki: sprawność - węgiel na stopniodzień i szacowana strata kominowa
- Kg węgla na grzewczy stopniodzień (baza 18 °C) dla okresu i dla każdego dnia: ten sam dom przy tej samej pogodzie powinien potrzebować tyle samo węgla, więc wzrost wskazuje na brudny kocioł
- Szacowana strata kominowa (Siegert, współczynniki dla węgla, założone 10 % CO2, 20 °C w kotłowni) z temperatury spalin w PRACY
- Trend: ostatnie 7 pełnych dni a 3 tygodnie wcześniej; sprawdzenie ostrzega przy +15 %
- Karta "Efektywność" z kafelkami, wykresem trendu z 30 dni i wyjaśnieniem; wartości dzienne tylko dla (prawie) pełnych dni

## 1.14.0 · 26.10.2026 13:04
- Historia: 7 dni z uśrednianiem, porównanie z wczoraj; szybsze statystyki
- API historii: max_points uśrednia wiersze w przedziałach czasu (liczby uśredniane, tryb = najczęstszy), czytane są tylko pliki z zakresu; strona prosi o najwyżej 900 punktów - 7 dni ładuje się jako ~840 punktów zamiast ~19k
- Strona historii: zakres "7 dni", "porównaj z wczoraj" (ta sama seria 24 h wcześniej, przerywana), prawdziwa oś czasu, luki w danych zostają puste, opis serii zadanej
- Statystyki: raport liczony z góry w tle po starcie i używany ponownie przez minutę (przycisk odświeżania liczy od nowa)
- Podgląd: ta sama obsługa max_points

## 1.13.0 · 26.10.2026 12:51
- Statystyki: energia w kWh, średnia i szczytowa moc cieplna
- Energia z węgla (kWh, na dzień), średnia moc cieplna (kW) i najwyższa moc z 5 min dla okresu; kWh na dzień w modelu pogodowym
- Dopisek na stronie: energia w paliwie (wartość opałowa), bez sprawności kotła
- Energia w danych demo liczona jak w module statystyk (bez współczynnika sprawności)

## 1.12.0 · 26.10.2026 12:49
- Zdalny podgląd pieca tylko do odczytu
- Nowy moduł pomocniczy "Podgląd zdalny" (remote_view, domyślnie wyłączony): wątek w tle czyta lokalne API (stan, tryb ręczny, przyrosty historii i logów, raport statystyk, ustawienia) i wysyła je skompresowane gzipem do podglądu; adres serwera i token z /etc/furnace-brain.env; tylko wysyła, nigdy nie odbiera
- Backend/viewer.py: osobna aplikacja serwująca niezmienione GUI na wysłanych danych, z tymi samymi kształtami API odczytu co na Pi; każdy zapis jest przyjmowany i ignorowany (odpowiedzi zawierają prawdziwe wartości); wysyłanie wymaga tokenu; opcjonalne hasło do oglądania; endpoint stanu zdrowia
- Render.yaml: usługa furnace-brain-view na gałęzi prod (token generowany przez Render)
- Testy podglądu (wysyłanie, token, ignorowane zapisy, gzip)

## 1.11.0 · 26.10.2026 12:33
- Nowa strona statystyk: węgiel, średnie, jakość regulacji, sprawdzenia, model pogodowy
- Backend/core/analytics.py: dzienne zestawienia z historii (wiersze co 30 s), porcji węgla i dziennika zdarzeń; minione dni zapamiętywane w analytics_days.json; okresy dziś / wczoraj / 7 d / 30 d / sezon
- GET /api/analytics/report: węgiel (łącznie, na dzień, na godzinę), średnie (moc, kocioł, grzejniki, spaliny, na zewnątrz, dmuchawa, podajnik), czas w poszczególnych trybach, rozpalenia, jakość regulacji w ustabilizowanej PRACY (w granicach ±1/±2 °C, średni uchyb, σ, maks. powyżej/poniżej, ocena), sprawdzenia stanu z ok/warn/bad oraz liniowy model węgla i mocy względem temperatury zewnętrznej
- Historia zapisuje zadaną PRACY ("zadana") na potrzeby statystyk regulacji
- Strona statystyk przebudowana wokół raportu (przyciski okresu, wykres dziennego węgla a temperatury zewnętrznej, wykres punktowy z linią trendu)
- Środowisko testowe: FURNACE_BRAIN_DEMO_DAYS generuje minione dni danych demo (tylko symulator), na Render ustawione na 30

## 1.10.0 · 26.10.2026 12:25
- Zwięzłe logi; serie dmuchawy i podajnika w historii
- Zawór mieszający: jeden wpis na ruch (kierunek, rzeczywisty czas pracy, CO a zadana, tryb) zamiast decyzji + startu + stopu; ochrona kotła: pierwsze zamknięcie plus podsumowanie, gdy się kończy
- Dmuchawa: bez wpisu przy każdej zmianie mocy; zmiany trybu + podsumowanie co 15 min (średnia moc, udział czasu pracy)
- Podajnik: bez wpisów ON/OFF; podsumowanie co 15 min (podania, łączny czas); ręczne podawanie w trybie RĘCZNYM logowane od razu
- Historia: "dmuchawa" (średni %) i "slimak" (% czasu podawania) w każdym wierszu, domyślnie wyłączone na wykresie
- Poprawka: zmiana ustawień dziennika zdarzeń przenosiła katalog logów do folderu z kodem, więc nowe wpisy znikały ze strony logów

## 1.9.3 · 26.10.2026 12:09
- Logi: pełne listy źródeł/poziomów z /logs/meta
- Nowe GET /api/logs/meta: każdy poziom zdarzeń i każde źródło (włączone moduły + kernel/aux_runner); strona logów już o to pytała i dostawała 404 - teraz wypełnia oba filtry z tego jednego zapytania, zamiast wyciągać źródła z najnowszych wpisów
- Filtr poziomu: dodany ALARM (z własnym kolorem), usunięty nieużywany DEBUG

## 1.9.2 · 26.10.2026 12:05
- Zbiornik CWU bliżej zasobnika; filtr źródła w logach przeszukuje wszystkie logi
- Narysowany na nowo zbiornik CWU przesuwa się w prawo, obok zasobnika paliwa, więc okno pogody ma więcej miejsca wokół
- Logi: filtr źródła jest wysyłany do backendu (który przeszukuje wszystkie pliki logów) zamiast filtrować tylko ostatnie N wpisów, a lista źródeł zawiera każdy moduł - rzadkie zdarzenia, np. modułu pogody, ginęły za częstymi wpisami podajnika/zaworu mieszającego

## 1.9.1 · 26.10.2026 12:00
- Okno pogody dalej na lewo od rury, temperatura na nią zachodzi

## 1.9.0 · 26.10.2026 11:56
- Temperatura zewnętrzna na stronie głównej: okno ze słońcem/księżycem

## 1.8.0 · 26.10.2026 11:18
- Temperatura zewnętrzna z Open-Meteo, zapisywana w historii
- Nowy moduł pomocniczy "weather" (Pogoda): lokalizacja w ustawieniach (domyślnie Nowy Sącz), pobiera bieżącą temperaturę z Open-Meteo co 15 min w wątku w tle z limitem 10 s - nigdy nie blokuje żadnej pętli; przy błędach ponawia po 5 min, jedno ostrzeżenie na awarię
- Moduły pomocnicze mogą publikować dane w SystemState.runtime; jądro używa runtime["weather"] jako sensors.outside_temp, gdy nie ma czujnika zewnętrznego, a dane są świeże (najwyżej 60 min)
- Historia zapisuje temp_zewn; /history/fields zwraca sumę nagłówków, więc nowa kolumna pojawia się obok starszych plików; dodany opis/kolor na wykresie
- Symulator zgłasza brak czujnika zewnętrznego, jak prawdziwy piec

## 1.7.0 · 26.10.2026 11:11
- Okienko kotła: jedna zadana, temperatura przełączenia i rozpalania ustawiane automatycznie

## 1.6.1 · 26.10.2026 11:05
- Pole wersji pokazuje się niezawodnie w Safari

## 1.6.0 · 26.10.2026 11:03
- Szczegóły logu w okienku; po wgraniu żadnych nieaktualnych plików z pamięci podręcznej
- Logi: dotknięcie wpisu otwiera okienko (pełna wiadomość, źródło, typ, dane zdarzenia jako wiersze klucz/wartość) zamiast rozwijać wiersz; zwarty pasek narzędzi na niskich ekranach, więc lista nadaje się do użycia na panelu 800x480
- Pliki statyczne są serwowane z "Cache-Control: no-cache" (Render i bramka na Pi), więc przeglądarka sprawdza je ponownie i nigdy nie miesza nowej strony ze starym arkuszem stylów z pamięci podręcznej (Safari pokazywało przełącznik podajnika jako czarny prostokąt)
- Przełącznik podajnika ma kolory zapisane także jako atrybuty svg

## 1.5.0 · 26.10.2026 10:56
- Temperatura przełączenia rozpalanie->praca w okienku kotła; przełącznik podajnika w zwykłym svg
- Okienko kotła: zadane pracy / przełączenia / rozpalania trzymane w kolejności (praca <= przełączenie <= rozpalanie), więc rozpalanie nie może utknąć
- Przełącznik "ŚLIMAK RĘCZNIE" rysowany jako zwykłe svg zamiast foreignObject, który Safari na iPadzie umieszczało w złym miejscu
- Nieco mniejsze okienka na bardzo niskich ekranach

## 1.4.0 · 26.10.2026 10:46
- Ręczny przełącznik podajnika na zasobniku paliwa

## 1.3.0 · 26.10.2026 10:36
- Okienko zadanej kotła na stronie głównej
- Zadana PRACY, wpisywana jednocześnie do PID, regulatora rozmytego i predykcyjnego (pokazywane, gdy obecnie się różnią, zapis je wyrównuje)
- Zadana ROZPALANIA, która nie może zejść poniżej zadanej PRACY (podniesienie PRACY ponad nią podnosi też ROZPALANIE)

## 1.2.0 · 26.10.2026 10:31
- Pompy i dmuchawa ustawiane ze strony głównej; okienka mieszczą się na niskich ekranach
- Okienko zadanej jest uniwersalne: jedno lub więcej pól na element schematu, zakresy/kroki/jednostki/opisy ze schematu modułu, zapisywane tylko zmienione pola
- Pompa CO i CWU: temperatura włączenia; dmuchawa: czas cyklu i obroty
- Wszystkie klikalne elementy dostają plakietkę z ołówkiem
- Okienka (także okienko podajnika) zmniejszają się na ekranach poniżej 900px wysokości, zamiast się nie mieścić; ustawienia wstępne podajnika w jednym rzędzie

## 1.1.2 · 26.10.2026 10:25
- Dopasowanie stron do widocznej wysokości ekranu na tabletach i telefonach

## 1.1.1 · 26.10.2026 10:21
- Mniejsza plakietka dotknięcia w kolorach plakietek strony

## 1.1.0 · 26.10.2026 10:18
- Oznaczenie klikalnych części strony głównej plakietką z ołówkiem
