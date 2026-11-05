# Paleta nowego GUI („Węgiel i bursztyn”)

Jedna paleta dla całego nowego GUI (`frontend/v2`). Nowe kolory tylko z niej.

**Reguła (jak w Adobe Color):** kolor bazowy to bursztyn HSL(45°, 86%, 57%). Kolory stanów mają **tę samą
nasyconość i jasność - HSL(h, 50%, 60%)** i różnią się tylko odcieniem, dlatego razem się zgadzają.
Wersje ciemne (tła) to HSL(h, 35%, 27%), bardzo ciemne HSL(h, 35%, 14-17%), jasne HSL(h, 70%, 78%).
Szarości mają lekko chłodny odcień HSL(215°, 10-12%, …).

| Rola | Odcień | Główny | Ciemny (tło) | Jasny |
|---|---|---|---|---|
| Akcent - bursztyn (aktywne, wartości, kocioł) | 45° | `#f0c033` | `#605329` | `#f6cf5a` |
| Ciepło - spaliny | 28° | `#cc9666` | `#5d432d` | `#eec4a0` |
| Dobrze / pracuje / Praca | 150° | `#66cc99` | `#2d5d45` | - |
| Problem / Stop | 8° | `#cc7466` | `#5d332d` | - |
| Zimno - grzejniki, powrót | 208° | `#669ccc` | `#2d465d` | `#a0caee` |

Szarości: tło `#0b0d10`, karta `#16191d`→`#2b2f36`, obrys `#383e47`, tekst `#f2f3f5`, opisy `#b0b6bf`, słabsze `#89929f`.

Elementy wzięte z obecnego GUI (kafelki i wykresy Statystyk / Serwisu) są przemalowywane na tę paletę tylko
w nowym GUI: reguły CSS na końcu `v2/pages.css`, wykresy w `v2/palette.js`.
