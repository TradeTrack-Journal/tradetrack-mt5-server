# Локальна підтримка demo build 6190 — 10.09.2026

На Windows VPS під TradeTrackWorker перевірено два MetaQuotes-Demo у власних
portable-каталогах demo-01 і demo-02. У паралельних cached-session пробах native
login/server/data_path/PID/creation time не змішувалися. Обидва термінали мали
English `Demo Account - Read Only - Hedge`, account.trade_allowed=false та
terminal.tradeapi_disabled=true.

Користувач вручну перевів demo-02 у master-режим: Read Only зник,
account.trade_allowed став true, Python trading залишився disabled. Investor-проба
відхилила його до initialize/читань. Окрема metadata-проба не читала history або
positions. Після повернення до investor паралельна позитивна проба повторно пройшла.

Локальний caption gate тепер допускає лише integer builds 6182 і 6190 зі
строгим English Demo Account форматом; для 6190 дозволено тільки Hedge.
Невідомий build, Real Account, інша мова,
неправильний login/server і master caption відхиляються. Wire payload містить
фактичний перевірений build; зміна build між identity checks відхиляє результат.
Nest resultSchema узгоджено допускає 6182 або 6190.

Це не production rollout і не криптографічна атестація пароля. Explicit Python
investor/master login ще не перевірено; native diagnostic використовував cached
session. Інші брокери, Netting на 6190, RDP disconnect/reboot, тривале навантаження,
broker timezone і повнота історії залишаються неперевіреними. Netting на 6190
відхиляється до окремої живої перевірки; поведінка 6182 збережена.
Collector API для demo ще потрібен; production URL не використовується для проб.

Локальні докази поза Git у `C:\TradeTrack\worker`: master-metadata/rejection,
demo-pair-isolation та demo-pair-restored JSON-звіти за 20260910. Вони містять
агрегати, не паролі. Canonical import залишається вимкненим.
