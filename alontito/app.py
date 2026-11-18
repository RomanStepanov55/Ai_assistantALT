"""Alontito - a terminal brain for a Roblox bot.

One application, one prompt: pick a game, launch it, watch the agent, let it
train, and read what it learned. Every number shown here comes from a read or
a measurement; when there is no number, the app says so instead of printing 0.

Commands are listed in /help. Free text is routed by brain.interpret() when it
is obvious ("запусти игру", "тренируйся"), and refused when it is not.
"""
import json
import os
import sys
import time
from pathlib import Path

from . import games_catalog
from .brain import Brain, interpret
from .memory import JOURNAL_FILE, STATUSES, Memory
from .skills import ALLOWED_ACTIONS, AlontitoBot, guard_report
from .mcp_server import AlontitoMCPServer
from .ui import (Timer, box, clear_screen, err, home_screen, info, ok, paint,
                 prompt, ready, say, set_background, step, table, term_width,
                 two_columns, warn, blank)

VERSION = "1.2.0"

HOME_COMMANDS = [
    ("/help", "справка"),
    ("/games", "каталог игр"),
    ("/play", "запустить игру"),
    ("/auto", "автопоиск игры"),
    ("/attach", "проверить HUD"),
    ("/status", "замер сейчас"),
    ("/train", "тренировка"),
    ("/watch", "наблюдение"),
    ("/learn", "улучшение"),
    ("/memory", "факты"),
    ("/skills", "навыки"),
    ("/journal", "журнал"),
    ("/safety", "запреты"),
    ("/mcp", "MCP-мост для ИИ"),
    ("/info", "о боте"),
    ("/about", "о проекте"),
    ("/github", "репозиторий"),
    ("/support", "связь"),
    ("/quit", "выход"),
]


class App:
    def __init__(self):
        self.memory = Memory()
        self.brain = Brain(self.memory)
        self.bot = AlontitoBot()
        self.mcp = AlontitoMCPServer(self.bot, self.memory)
        self.running = True
        self.health = {}
        self._selected = self.memory.selected_game()
        self._background = True
        self._autostart = bool(self.memory.data.get("autostart", True))
        self._mcp_enabled = bool(self.memory.data.get("mcp_enabled", False))

    # ------------------------------------------------------------------
    # shell
    # ------------------------------------------------------------------

    def start(self):
        self.refresh_home()
        if self._autostart:
            self.auto_attach()
        while self.running:
            print(ready())
            raw = prompt("  › ")
            line = (raw or "").strip()
            if not line:
                continue
            command = interpret(line)
            if command is None:
                warn("не понял формулировку — /help. Свободный текст понимаю "
                     "так: «запусти игру», «тренируйся», «что видишь», "
                     "«как дела», «учись».")
                continue
            self.dispatch(command, line)

    def refresh_home(self):
        clear_screen()
        facts = len(self.memory.facts())
        questions = len(self.memory.open_questions())
        skills = len(self.memory.skills())
        game = (self._selected or {}).get("name") or "не выбрана"
        print(home_screen(VERSION, game, facts, questions, skills,
                          HOME_COMMANDS, mcp_active=self._mcp_enabled))
        print()

    def dispatch(self, command: str, raw: str):
        name, _, rest = command.partition(" ")
        args = rest.split() if rest.strip() else []
        handler = {
            "/help": self.cmd_help,
            "/games": self.cmd_games,
            "/play": self.cmd_play,
            "/auto": self.cmd_auto,
            "/attach": self.cmd_attach,
            "/status": self.cmd_status,
            "/train": self.cmd_train,
            "/watch": self.cmd_watch,
            "/learn": self.cmd_learn,
            "/memory": self.cmd_memory,
            "/skills": self.cmd_skills,
            "/journal": self.cmd_journal,
            "/safety": self.cmd_safety,
            "/mcp": self.cmd_mcp,
            "/info": self.cmd_info,
            "/about": self.cmd_info,
            "/github": self.cmd_github,
            "/support": self.cmd_support,
            "/theme": self.cmd_theme,
            "/home": self.cmd_home,
            "/quit": self.cmd_quit,
            "/exit": self.cmd_quit,
        }.get(name)
        if handler is None:
            err(f"неизвестная команда: {name}. Используй /help")
            return
        try:
            handler(args)
        except KeyboardInterrupt:
            blank()
            warn("прервано пользователем — действие остановлено")
        except Exception as e:                      # never kill the shell
            err(f"ошибка выполнения: {e}")

    # ------------------------------------------------------------------
    # commands
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # autostart: find Roblox, identify the place, open what is needed
    # ------------------------------------------------------------------

    def auto_attach(self):
        blank()
        info("автопоиск: Roblox → плейс → игра")
        preferred = None
        sel = self._selected or {}
        if sel.get("place_id"):
            preferred = games_catalog.find(str(sel["place_id"]))

        result = games_catalog.autostart(preferred=preferred, on_step=step)
        blank()
        game = result.get("game")
        place = result.get("place") or {}

        if not result.get("attached"):
            warn(f"не подключился: {result.get('detail')}")
            if not result.get("launched"):
                step("Roblox может быть не установлен: проверь клиент и вход в аккаунт")
            step("когда игра откроется — /attach, или вручную /play muscle")
            blank()
            return

        if game:
            if result.get("launched"):
                ok(f"открыл и подтвердил: {game.name}")
            else:
                ok(f"игра уже на экране: {game.name}")
        elif place.get("found"):
            warn(f"на экране place {place['place_id']} — в каталоге его нет")
            step("имя окна не считается доказательством, поэтому игру не называю")
        elif result.get("launched"):
            ok("клиент открыт, HUD игры подтверждён")
        else:
            ok("игра уже на экране")

        identity = result.get("identity") or ""
        if place.get("found") and "другой place" not in identity:
            ok(f"place {place['place_id']} · {identity}")
            step(f"лог: {place.get('log')} · возраст {place.get('age_sec')}с")
        elif "другой place" in identity:
            err(f"идентичность не совпала: {identity}")
            step("это ровно тот случай, из-за которого имя игры не считается доказательством")
        else:
            warn("place id логом не подтверждён: HUD виден, но какой именно плейс — "
                 "не доказано")

        if game and (not sel or sel.get("place_id") != game.place_id):
            self.memory.select_game(game.name, game.place_id)
            self._selected = self.memory.selected_game()
            step(f"выбранная игра обновлена: {game.name}")

        self.health = {"is_game": True}
        info("дальше: /status — числа, /train 2 — тренировка")
        blank()

    def cmd_auto(self, args):
        what = (args[0].lower() if args else "")
        if what in ("on", "вкл", "да"):
            self._autostart = True
            self.memory.data["autostart"] = True
            self.memory.save()
            ok("автопоиск включён: при следующем запуске найду Roblox и открою игру")
            blank()
            return
        if what in ("off", "выкл", "нет"):
            self._autostart = False
            self.memory.data["autostart"] = False
            self.memory.save()
            ok("автопоиск выключен: запускаю игру только по /play")
            blank()
            return
        if what in ("now", "сейчас"):
            self.auto_attach()
            return
        blank()
        info(f"автопоиск: {'включён' if self._autostart else 'выключен'}")
        step("/auto on — включить, /auto off — выключить, /auto now — найти сейчас")
        blank()

    def cmd_help(self, args):
        blank()
        width = term_width()
        say(box("КОМАНДЫ", [
            two_columns([
                ("/games", "каталог по Place ID"),
                ("/play <ключ|id>", "официальная ссылка"),
                ("/auto", "автопоиск: Roblox и плейс"),
                ("/attach", "доказать HUD в кадре"),
                ("/status", "замер: сила, гемы"),
                ("/train [подходы]", "гантели, с замером"),
                ("/watch [секунды]", "наблюдение без действий"),
                ("/learn", "одно улучшение"),
                ("/memory [слово]", "факты и статусы"),
                ("/skills", "успех и прирост/мин"),
                ("/journal [n]", "события журнала"),
                ("/safety", "границы действий"),
                ("/home", "главный экран"),
                ("/theme", "фон вкл/выкл"),
                ("/quit", "выход"),
            ], width - 6),
            "",
            paint("Почему не «просто нажми кнопку»: сила читается из HUD, и "
                  "подход без подтверждённого числа не считается успехом.",
                  "muted", dim=True),
        ], width=width))
        blank()

    # ------------------------------------------------------------------ games

    def cmd_games(self, args):
        blank()
        rows = []
        selected = (self._selected or {}).get("place_id")
        for game in games_catalog.list_games():
            mark = "→" if game.place_id == selected else " "
            rows.append([mark, game.key, game.name, str(game.place_id),
                         game.note])
        say(box("ИГРЫ", [
            table(["", "ключ", "название", "place id", "заметка"], rows,
                  width=term_width() - 8),
            "",
            "Игра определяется по Place ID. Одинаковые названия у разных мест — "
            "обычная история; по названию эксперимент уедет в другую игру.",
        ], width=term_width()))
        blank()
        info("запуск: /play muscle    либо: /play <place id>")
        blank()

    def cmd_play(self, args):
        if not args:
            err("укажи игру: /play muscle  (см. /games)")
            return
        key = args[0]
        game = games_catalog.find(key)
        if game is None and key.isdigit():
            game = games_catalog.add_custom(f"Place {key}", int(key))
        if game is None:
            err(f"игра «{key}» не найдена. /games покажет ключи, либо передай "
                f"Place ID числом")
            return

        blank()
        info(f"запуск: {game.name} · place {game.place_id}")
        launched, detail = games_catalog.launch(game)
        if not launched:
            err(f"не удалось открыть ссылку: {detail}")
            return
        ok(f"ссылка отправлена: {paint(detail, 'muted')}")
        self.memory.select_game(game.name, game.place_id)
        self._selected = self.memory.selected_game()

        info("жду HUD игры в текущем кадре (не «окно открылось», а бейджи видны)")
        with Timer() as t:
            confirmed, why = games_catalog.wait_for_game(timeout=150, poll=4)
        if confirmed:
            ok(f"HUD подтверждён за {t.seconds:.0f}s")
        else:
            warn(f"за {t.seconds:.0f}s HUD не подтверждён: {why}")
            step("окно Roblox может быть на экране загрузки или в меню — это "
                 "не доказательство игры")
            step("проверь вручную и повтори /attach")
        blank()

    # ----------------------------------------------------------------- attach

    def cmd_attach(self, args):
        blank()
        state = games_catalog.window_state()
        if not state.get("found"):
            err(state.get("detail", "окно не найдено"))
            step("сначала /play <игра>, либо открой игру сам")
            blank()
            return
        ok(f"окно найдено, клиентская область {state['client']}")
        try:
            from core.window import WindowManager
            from vision.eye import Eye
            eye = Eye(WindowManager())
            with Timer() as t:
                frame = eye.capture()
                is_game = bool(frame is not None and eye.frame_is_game(frame))
            if is_game:
                ok(f"HUD игры виден в текущем кадре ({t.seconds:.1f}s)")
                self.health["is_game"] = True
            else:
                warn(f"HUD не подтверждён ({t.seconds:.1f}s): это кадр не игры — "
                     f"загрузка, меню Roblox или вкладка клиента")
                self.health["is_game"] = False
        except Exception as e:
            err(f"проверка кадра не удалась: {e}")
        blank()

    # ----------------------------------------------------------------- status

    def _probe(self):
        """Read the current state without printing a panel. Returns (result, seconds)."""
        with Timer() as t:
            res = self.bot.health()
        self.health = {"is_game": res.success,
                       "strength": res.metrics.get("strength"),
                       "gems": res.metrics.get("gems")}
        return res, t.seconds

    def cmd_status(self, args):
        blank()
        res, secs = self._probe()
        s, g = res.metrics.get("strength"), res.metrics.get("gems")
        lines = [
            res.detail,
            "",
            (paint(f"сила  {s:,}", "lilac", bold=True) if s is not None
             else paint("сила  не прочитана — это не ноль, это отсутствие "
                        "данных", "muted")),
            (paint(f"гемы  {g:,}", "lilac", bold=True) if g is not None
             else paint("гемы  не прочитаны — два кадра не совпали", "muted")),
            paint(f"время {secs:.1f}s · статус: {res.evidence}", "muted"),
        ]
        say(box("СОСТОЯНИЕ", lines, width=term_width(),
                color="green" if res.success else "yellow"))

        decision = self.brain.plan(self.health, attached=self.bot.is_attached())
        blank()
        info(f"мозг предлагает: {paint(decision.action, 'violet', bold=True)} — "
             f"{decision.reason}")
        if decision.parameters.get("hint"):
            step(f"подсказка: {decision.parameters['hint']}")
        blank()

    # ------------------------------------------------------------------ train

    def cmd_train(self, args):
        sets = 3
        if args:
            try:
                sets = max(1, min(20, int(args[0])))
            except ValueError:
                err("количество подходов должно быть числом")
                return

        blank()
        res, secs = self._probe()        # no blind training: measure first
        if not res.success:
            err(f"тренировка отменена: {res.detail}. Клики в чужое окно "
                f"запрещены.")
            blank()
            return
        if self.health.get("strength") is None:
            err("тренировка отменена: сила не читается — прирост нельзя будет "
                "подтвердить, а неподтверждённый подход не считается успехом")
            blank()
            return

        before = self.health.get("strength")
        info(f"HUD подтверждён за {secs:.1f}s · сила {before:,}")
        info(f"план: {sets} подход(ов) по 20 повторов, действие TRAIN_DUMBBELLS "
             f"(разрешено: {sorted(ALLOWED_ACTIONS)})")
        blank()

        def on_event(row):
            mark = "✔" if row.get("success") else "✖"
            gain = row.get("gain")
            gain_text = (f"+{gain:,}" if isinstance(gain, int) and gain > 0
                         else "нет числа")
            colour = "green" if row.get("success") else "yellow"
            sys.stdout.write(
                "  " + paint(mark, colour, bold=True) +
                f" подход {row['set']}/{sets}  " +
                paint(f"{gain_text:>12}", colour) +
                f"  {row['sec']:>5}s  " +
                paint(row.get("detail") or row.get("error") or "", "muted") +
                "\n")
            sys.stdout.flush()

        with Timer() as t:
            res = self.bot.train(sets=sets, on_event=on_event)

        blank()
        if res.success:
            ok(res.detail)
            self.memory.record_skill("train", True, reward=res.reward or 0,
                                     seconds=res.seconds,
                                     notes=f"{sets} подходов")
            self.memory.record_run(res.reward, res.seconds,
                                   note=f"train {sets}x20")
        else:
            warn(res.detail)
            self.memory.record_skill("train", False, reward=0,
                                     seconds=res.seconds, error=res.error)

        verdict = self.brain.reflect("train", before,
                                     res.metrics.get("after"), t.seconds)
        blank()
        say(box("РЕФЛЕКСИЯ", [
            verdict.text,
            "",
            paint(f"вердикт: {verdict.verdict} · базовая линия: "
                  f"{verdict.baseline if verdict.baseline else 'ещё не задана'}",
                  "muted"),
        ], width=term_width(),
            color="green" if verdict.verdict == "лучше" else "yellow"))
        if verdict.rollback:
            warn("изменение ухудшило результат — откатывай правку, не оставляй "
                 "«вроде работает»")
        blank()

        proposal = self.brain.propose()
        if proposal:
            info(f"следующее улучшение (одно): {proposal['change']}")
            step(f"проблема: {proposal['problem']} · метрика: {proposal['metric']}")
            step(f"откат: {proposal['rollback']}")
            blank()

    # ------------------------------------------------------------------ watch

    def cmd_watch(self, args):
        seconds = 30
        if args:
            try:
                seconds = max(5, min(600, int(args[0])))
            except ValueError:
                err("секунды должны быть числом")
                return
        blank()
        info(f"наблюдение {seconds}s без действий. Остановить: Ctrl+C")
        blank()
        readings = []
        t0 = time.time()
        try:
            while time.time() - t0 < seconds:
                with Timer() as t:
                    res = self.bot.observe()
                s = res.metrics.get("strength")
                g = res.metrics.get("gems")
                modal = res.metrics.get("modal_open")
                stamp = time.strftime("%H:%M:%S")
                num = f"{s:,}" if s is not None else "не прочитана"
                gems = f"{g:,}" if g is not None else "—"
                line = (f"{stamp}   сила {num:>13}   гемы {gems:>12}   "
                        f"диалог {'да' if modal else 'нет':>3}   {t.seconds:.1f}s")
                sys.stdout.write("  " + paint(line, "muted") + "\n")
                sys.stdout.flush()
                readings.append({"t": time.time() - t0, "strength": s,
                                 "gems": g, "modal": bool(modal)})
                time.sleep(max(0.0, 4.0 - t.seconds))
        except KeyboardInterrupt:
            blank()
            warn("наблюдение остановлено")
        blank()

        known = [r for r in readings if r["strength"] is not None]
        if len(known) >= 2:
            delta = known[-1]["strength"] - known[0]["strength"]
            span = max(0.1, known[-1]["t"] - known[0]["t"])
            ok(f"{len(known)}/{len(readings)} кадров с числом; сила "
               f"{known[0]['strength']:,} → {known[-1]['strength']:,} "
               f"({delta:+,} за {span:.0f}s)")
            if delta > 0:
                rate = delta / span * 60
                self.memory.record_run(delta, span, note="watch")
                info(f"темп наблюдения: {rate:,.0f}/мин")
        elif readings:
            warn(f"чисел нет: {len(readings)} кадров, сила не прочитана. Это не "
                 f"ноль и не «всё сломалось» — это отсутствие данных.")
        self.memory.journal("watch", frames=len(readings), known=len(known))
        blank()

    # ------------------------------------------------------------------ learn

    def cmd_learn(self, args):
        blank()
        summary = self.brain.summary()
        runs = self.brain.last_runs(5)
        avg = summary["avg_per_minute"]
        rows = [[r["at"][-8:], f"{r['gain']:,}" if r.get("gain") else "—",
                 str(r.get("per_minute") or "—"), str(r.get("seconds") or "—"),
                 (r.get("note") or "")[:22]] for r in runs]
        say(box("ЧТО ИЗВЕСТНО", [
            f"сессий: {summary['sessions']} · прогонов: {summary['runs']} "
            f"(подтверждённых {summary['verified_runs']})",
            f"суммарный подтверждённый прирост: {summary['total_gain']:,}",
            ("средний темп: " + f"{avg:,}/мин") if avg else
            "средний темп: нет данных",
            f"лучший навык: {summary['best_skill'] or 'нет замеров'}",
            "",
            table(["время", "прирост", "в мин", "сек", "заметка"], rows,
                  width=term_width() - 8)
            if rows else "прогонов пока нет — сначала /train или /watch",
        ], width=term_width()))
        blank()

        proposal = self.brain.propose()
        if proposal is None:
            info("улучшений не предлагаю: недостаточно замеров. Нужен хотя бы "
                 "один подтверждённый подход (/train 1).")
        else:
            say(box("ОДНО УЛУЧШЕНИЕ ЗА ИТЕРАЦИЮ", [
                f"проблема:  {proposal['problem']}",
                f"изменение: {proposal['change']}",
                f"метрика:   {proposal['metric']}",
                f"откат:     {proposal['rollback']}",
                f"основание: {proposal['evidence']}",
            ], width=term_width(), color="cyan"))
            step("правило: замер до → ровно это изменение → замер после. "
                 "Хуже — откат.")
        blank()

        questions = self.memory.open_questions()
        if questions:
            info(f"открытых вопросов: {len(questions)}")
            for q in questions[:5]:
                step(f"{q['question']} → {q['metric']}")
            blank()

    # ----------------------------------------------------------------- memory

    def cmd_memory(self, args):
        needle = " ".join(args)
        items = (self.memory.find_facts(needle) if needle
                 else self.memory.facts())
        blank()
        if not items:
            warn("фактов нет" if not needle
                 else f"по «{needle}» ничего не найдено")
            blank()
            return
        rows = [[f["date"], f["status"][:30], f["text"][:44], f["source"][:22]]
                for f in items[-25:]]
        say(box("ПАМЯТЬ", [
            table(["дата", "статус", "факт", "источник"], rows,
                  width=term_width() - 8),
        ], width=term_width()))
        counts = {s: len(self.memory.facts(s)) for s in STATUSES}
        numbered = " · ".join(f"{s[:22]} = {n}"
                              for s, n in counts.items() if n)
        if numbered:
            say("  " + paint(numbered, "muted"))
        blank()

    def cmd_skills(self, args):
        blank()
        skills = self.memory.skills()
        if not skills:
            warn("навыки ещё не запускались: статистики нет. Это не нули.")
            blank()
            return
        rows = [s.row() for s in skills]
        say(box("НАВЫКИ", [
            table(["навык", "попытки", "успех", "прирост/мин", "сек/попытка",
                   "последняя ошибка"], rows, width=term_width() - 8),
            "",
            paint("неподтверждённые попытки не попадают в успех: пусто ≠ ноль",
                  "muted", dim=True),
        ], width=term_width()))
        blank()

    def cmd_journal(self, args):
        n = 15
        if args:
            try:
                n = max(1, min(200, int(args[0])))
            except ValueError:
                err("количество должно быть числом")
                return
        path = Path(JOURNAL_FILE)
        blank()
        if not path.exists():
            warn("журнал пуст — событий ещё не было")
            blank()
            return
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for raw in lines[-n:]:
            try:
                item = json.loads(raw)
            except Exception:
                say("  " + paint(raw[:100], "muted"))
                continue
            kind = item.get("kind", "?")
            at = item.get("at", "")[-8:]
            rest = {k: v for k, v in item.items()
                    if k not in ("kind", "at", "ts")}
            text = ", ".join(f"{k}={v}" for k, v in list(rest.items())[:4])
            say(f"  {paint(at, 'muted')}  {paint(kind.ljust(11), 'violet')} "
                f"{text[:84]}")
        blank()

    def cmd_safety(self, args):
        blank()
        say(box("ЗАПРЕЩЕНО СТРУКТУРНО", guard_report(), width=term_width(),
                color="yellow"))
        blank()
        say(box("КАК ЭТО ДЕРЖИТСЯ", [
            f"1. Недоступные действия не вызываются: ALLOWED_ACTIONS = "
            f"{sorted(ALLOWED_ACTIONS)}",
            "2. Клики только в белый список областей; ESC и TAB не нажимаются",
            "3. Перед любым действием кадр проверяется: HUD игры должен быть "
            "виден в текущем кадре, иначе действие отклоняется",
            "4. Трейды и покупки не реализованы вовсе — не «выключены флагом»",
        ], width=term_width(), color="green"))
        blank()

    def cmd_mcp(self, args):
        """MCP server configuration and control command."""
        blank()
        sub = (args[0].lower() if args else "status")
        if sub in ("on", "enable", "start"):
            self._mcp_enabled = True
            self.memory.data["mcp_enabled"] = True
            self.memory.save()
            ok("MCP-мост активирован! Внешние ИИ (Claude, Cursor, Antigravity) могут подключаться.")
            info("Запуск в режиме stdio-сервера: python -m alontito.mcp_server")
        elif sub in ("off", "disable", "stop"):
            self._mcp_enabled = False
            self.memory.data["mcp_enabled"] = False
            self.memory.save()
            ok("MCP-мост отключен.")
        else:
            status_text = paint("АКТИВЕН", "green", bold=True) if self._mcp_enabled else paint("ВЫКЛЮЧЕН", "muted")
            tools = self.mcp.list_tools()
            tools_list = [f"• {t['name']}: {t['description'][:50]}…" for t in tools]
            say(box("MODEL CONTEXT PROTOCOL (MCP)", [
                f"Статус: {status_text}",
                f"Протокол: {self.mcp.get_server_info()['protocolVersion']} | Инструментов: {len(tools)}",
                "",
                paint("Команды управления:", "lilac", bold=True),
                "  /mcp on   — включить MCP-мост",
                "  /mcp off  — выключить MCP-мост",
                "",
                paint("Доступные MCP-инструменты для внешних ИИ:", "lilac", bold=True),
            ] + tools_list + [
                "",
                paint("Для подключения добавьте в config внешнего агента команду:", "muted"),
                paint(f"  python {Path(__file__).resolve().parent / 'mcp_server.py'}", "violet"),
            ], width=term_width(), color="purple"))
        blank()

    def cmd_info(self, args):
        blank()
        say(box("О БОТЕ", [
            f"Алонтито v{VERSION} · open-source · MIT",
            "Терминальный агент для Roblox: запуск игры по Place ID, чтение HUD, "
            "тренировка с подтверждённым приростом, память с источниками.",
            "",
            paint("Он не угадывает: нет числа — пишет «не подтверждено». Одно "
                  "улучшение за итерацию, замер до и после, откат если хуже.",
                  "muted"),
        ], width=term_width()))
        blank()

    def cmd_github(self, args):
        blank()
        root = Path(__file__).resolve().parent.parent
        is_repo = (root / ".git").exists()
        say(box("РЕПОЗИТОРИЙ", [
            f"путь: {root}",
            ("git-репозиторий инициализирован" if is_repo else
             paint("git не инициализирован — удалённого репозитория нет. "
                   "Ссылку не выдумываю: её нужно создать и указать.", "muted")),
            "",
            paint("пока репозитория нет, откат делается вручную: копии "
                  "data/memory.json, data/capabilities.json, "
                  "data/glyph_templates.npz", "muted"),
        ], width=term_width()))
        blank()

    def cmd_support(self, args):
        blank()
        say(box("СВЯЗЬ", [
            "канал поддержки не настроен: в проекте нет ни почты, ни сервера.",
            paint("Я не подставляю выдуманный адрес. Если нужен канал — его "
                  "надо завести и прописать здесь.", "muted"),
            "",
            "что можно сделать сейчас: /journal покажет события, /memory — "
            "факты, /safety — ограничения.",
        ], width=term_width()))
        blank()

    def cmd_theme(self, args):
        self._background = not self._background
        set_background(self._background)
        self.refresh_home()
        info("фон " + ("включён" if self._background else "выключен"))

    def cmd_home(self, args):
        self.refresh_home()
        blank()

    def cmd_quit(self, args):
        self.running = False
        self.memory.journal("quit", version=VERSION)
        self.memory.save()
        blank()
        info("состояние сохранено. До связи.")
        blank()


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
    app = App()
    try:
        app.memory.session_tick()
        app.start()
    except KeyboardInterrupt:
        blank()
        info("выход по Ctrl+C")
    except EOFError:
        blank()
        info("поток ввода закрыт")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
