"""mc_help: the DSL reference. Every ``` block is DSL that tests parse (and examples recalc clean)."""
from __future__ import annotations

OVERVIEW = """\
Mathcad 15 DSL: одна инструкция = один регион, сверху вниз. Пустые строки и // комментарии игнорируются.
Номера [n] в ответах — порядок регионов; их передавайте в mc_edit (start, end) и mc_read (regions).
```
# Заголовок
text: обычный абзац
L.b := 6 m
q := 12 kN/m
f(x, y) := x*y
M := q*L.b^2/8
M = ? kN*m
x^2 - 1 | factor -> ?
```
:= определение, :== глобальное, = ? вычислить (можно с единицей), -> ? символьно.
Блоки (программы, Given) — строки с отступом 4 пробела.
Темы mc_help: syntax, units, program, solve, plot, symbolic, examples."""

TOPICS = {
    "syntax": """\
Операторы: + - * / ^, унарный -, ! (факториал), < > <= >= != ==, and, or.
Числа 1.5e-3, комплексные 3 - 7i и 2i, строки "текст", бесконечность ∞.
Имена: буквы любого алфавита (σ, Δx), штрих y' — часть имени; литеральный индекс через точку: M.max, σ.adm.
```
z := 3 - 7i
Mt := [1, 2; 3, 4]
v := [1; 2; 3]
n := Mt[1, 0]
c0 := Mt^<0>
Tr := transpose(Mt)
i := 0..2
w[i] := i^2
t := 0, 0.1..1
f(x) := if(x > 0, x, -x)
Int1 := integral(x^2, x, 0, 1)
d(x) := deriv(sin(x), x, 2)
s1 := sum(k^2, k, 1, 10)
limit(sin(x)/x, x, 0) -> ?
r := sqrt(2) + nthroot(27, 3) + abs(-1) + det(Mt) + vsum(v)
```
Матрица пишется по строкам: [1, 2; 3, 4]; столбец [1; 2; 3]; внутри [ ] можно переносить строку.
Индексация с 0 (ORIGIN := 1 — с единицы). Столбец матрицы A^<j>, элемент A[i, j].
Анализ: integral(e, x, a, b) и integral(e, x) — неопределённый; deriv(e, x[, n]);
sum(e, i, a, b), sum(e, i) — по диапазону i; prod(...) так же; limit(e, x, a[, "+"|"-"]) — только с -> ?.
Ещё операторы-функции: transpose(A), vectorize(e), cross(a, b), conj(z). Остальные функции Mathcad пишутся
как есть: sin, ln, max, length, last, stack, augment, submatrix, root, polyroots, rkfixed…
Сравнение с отрицательным — с пробелом: x < -1 (слитно <- значит присваивание в программе).
Всё, чего нет в DSL: raw: <ml:…> (элемент math30) или raw: <region …>…</region>.""",

    "units": """\
Число и идущее за ним имя единицы перемножаются; после скобки, матрицы или переменной — явное *.
```
L.b := 6 m
q := 12 kN/m
a := 9.81 m/s^2
S.p := 2 m^2
φ := 30 deg
v := [1; 2; 3]*m
d := (L.b + 2 m)*2
M := q*L.b^2/8
M = ? kN*m
σ := 140 MPa
σ = ? kgf/cm^2
```
Без единицы вывода Mathcad покажет SI: M = ? → 54000 J. Нужные единицы — после ?: M = ? kN*m.
Проверено в Mathcad 15: m cm mm km in ft, s min hr, kg gm tonne lb, N kN MN kgf lbf,
Pa kPa MPa GPa psi atm bar, J W kW, A V Ω (или ohm) Hz K, deg rad, L mL.
Нет единицы kJ — пишите 1000 J. g — это ускорение свободного падения, грамм — gm.
Имя переменной, совпадающее с единицей или константой (L, A, S, F, s, m, N, K, T, V, W, J, C, H, c, g…), её
переопределяет: ⚠ «переопределена встроенная единица». Берите L.b, A.s, Ls и т. п.
То же с именами функций (I1, J0, Y0 — функции Бесселя): ⚠ «переопределена встроенная функция».
Несовместимые единицы дают ✗ «несовместимые единицы: Length вместо Force» и подобное.""",

    "program": """\
Программа — правая часть := с телом на следующих строках с отступом 4 пробела.
Последняя строка тела — значение программы.
```
f(n) :=
    s <- 0
    for i in 1..n:
        s <- s + i^2
    s
sgn(x) :=
    if x > 0:
        return 1
    else:
        if x < 0:
            return -1
    0
h(v) :=
    s <- 0
    for i in 0..last(v):
        if v[i] < 0: continue
        s <- s + v[i]
    while s > 100:
        s <- s/2
    try:
        s <- 1/s
    on error:
        return 0
    s
f(10) = ?
```
Конструкции: x <- e (локальная переменная), for i in a..b:, while c:, if c: / else:,
return e, break, continue, try: / on error:. Двоеточие обязательно.
Короткое тело — в той же строке: if v[i] < 0: continue.
Бесконечный цикл прерывается по таймауту (MATHCAD_TIMEOUT, по умолчанию 120 с).""",

    "solve": """\
Given — блок ограничений: строки с отступом, = в нём означает равенство. Приближения — до Given.
```
x := 1
y := 1
Given
    x^2 + y^2 = 6
    x = y
sol := Find(x, y)
sol = ?
p(z) := z^2 - 2
r1 := root(p(z), z, 0, 3)
r1 = ?
rts := polyroots([-2; 0; 1])
rts = ?
D(t, Y) := [Y[1]; -Y[0]]
Z := rkfixed([1; 0], 0, 10, 100, D)
Z[100, 1] = ?
```
Также Minerr(x, …) (лучшее приближение, если точного решения нет), Maximize(f, x), Minimize(f, x)
с Given или без. root(e, x) без отрезка ищет от текущего значения x.
polyroots(v) — корни v[0] + v[1]·x + v[2]·x^2 + …
ОДУ: D(t, Y) — вектор производных, rkfixed(Y0, t0, t1, N, D) или Rkadapt(...);
результат — матрица: Z^<0> время, Z^<1> первая функция…
Символьно: x^3 - 2 | solve, x -> ? (тема symbolic).""",

    "plot": """\
```
plot: x=t, y=sin(t), cos(t), t=0..10
plot: x=u, y=u^2, u=-2, -1.9..2, xlim=-2..2, ylim=0..4
i := 0..9
w[i] := i^2
plot: x=i, y=w[i]
```
x, y — выражения через запятую, до 16 трасс; если x меньше, чем y, одна x идёт на все y.
t=a..b (любое имя, кроме x/y/xlim/ylim) создаёт перед графиком регион t := a, a + (b - a)/200..b;
свой шаг: u=-2, -1.9..2. xlim=a..b, ylim=a..b — фиксированные пределы, без них автомасштаб.
Данные из матрицы: plot: x=Z^<0>, y=Z^<1>.
Величины с единицами Mathcad рисует в SI; поделите на единицу: y=M(xx)/(kN*m).""",

    "symbolic": """\
e -> ? — символьно; ключевые слова через |, их аргументы через запятую, цепочка | … | ….
```
(x + 1)^3 | expand -> ?
x^2 - 1 | factor -> ?
x^3 - 2 | solve, x -> ?
x^3 - 2 | solve, x | float, 5 -> ?
x^2 + y | substitute, x = 2 -> ?
sin(x) | series, x, 4 -> ?
sqrt(x^2) | assume, x > 0 | simplify -> ?
1/(x^2 - 1) | parfrac, x -> ?
x^2 + a*x + x | collect, x -> ?
x^2 + 3*x | coeffs, x -> ?
exp(-t) | laplace, t -> ?
integral(x^2, x) -> ?
deriv(sin(x)^2, x) -> ?
sum(1/k^2, k, 1, ∞) -> ?
e1(x) := x^2 - 1 | factor -> ?
```
Ещё: simplify, float, invlaplace, fourier, invfourier, ztrans, invztrans, combine, convert, explicit.
Символьный движок берёт значения определённых выше переменных: для чистой алгебры не задавайте x.
Присвоить символьный результат — функцией: e1(x) := … -> ? (e1 := … с неопределённым x даст ✗).""",
}

EXAMPLES = """\
# Балка на двух опорах
text: Равномерная нагрузка, проверка прочности и прогиба.
L.b := 6 m
q := 12 kN/m
E.s := 200 GPa
I.x := 8000 cm^4
W.x := 400 cm^3
M.max := q*L.b^2/8
M.max = ? kN*m
σ := M.max/W.x
σ = ? MPa
f.max := 5*q*L.b^4/(384*E.s*I.x)
f.max = ? mm
ok := if(σ <= 240 MPa, "прочность обеспечена", "не проходит")
ok = ?
M(z) := q*z*(L.b - z)/2
plot: x=xx/m, y=M(xx)/(kN*m), xx=0 m..L.b
## Матрицы и система
Ka := [4, -2; 1, 1]
b := [2; 3]
X := Ka^-1*b
X = ?
## Нелинейная система
u := 1
v := 1
Given
    u^2 + v^2 = 6
    u = v
sol := Find(u, v)
sol = ?
## Программа
n.p(N) :=
    k <- 0
    for i in 1..N:
        if mod(i, 3) == 0: k <- k + 1
    k
n.p(100) = ?
## Символьно
x^2 - 5*x + 6 | factor -> ?"""

TOPICS["examples"] = ("Полный лист, считается в Mathcad 15 без ошибок (mc_write его как есть):\n"
                      "```\n" + EXAMPLES + "\n```")


def text(topic: str | None = None) -> str:
    key = (topic or "").strip().lower()
    if not key:
        return OVERVIEW
    if key in TOPICS:
        return TOPICS[key]
    return f"нет темы «{topic}»; есть: {', '.join(TOPICS)}\n\n{OVERVIEW}"
