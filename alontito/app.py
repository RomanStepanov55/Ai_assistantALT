"""Alontito - a terminal brain for the Roblox companion.

One application, one prompt: pick a game, launch it (official link), watch the
USER's play, and read what was measured. Alontito does not play for the user
(HARD RULE #2, 2026-11-24): input to the game is disabled at the
InputController level. Every number shown here comes from a read or a
measurement; when there is no number, the app says so instead of printing 0.

Commands are listed in /help. Free text is routed by brain.interpret() when it
is obvious ("запусти игру", "что видишь"), and refused when it is not.
"""
import json
import os
import sys
import time
from pathlib import Path

from . import games_catalog
from . import omni_link
from .brain import Brain, interpret
from .memory import JOURNAL_FILE, STATUSES, Memory
from .skills import ALLOWED_ACTIONS, AlontitoBot, guard_report
from .mcp_server import AlontitoMCPServer
from .pet_terminal import pet3d
from .ui import (Timer, box, clear_screen, clip, err, home_screen, info, ok,
                 paint, prompt, ready, say, set_background, step, table,
                 term_width, two_columns, warn, blank)

VERSION = "1.6.0"

HOME_COMMANDS = [
    ("/help", "справка"),
    ("/games", "каталог игр"),
    ("/play", "запустить игру"),
    ("/auto", "автопоиск игры"),
    ("/attach", "проверить HUD"),
    ("/status", "замер сейчас"),
    ("/watch", "наблюдение"),
    ("/learn", "улучшение"),
    ("/memory", "факты"),
    ("/skills", "навыки"),
    ("/journal", "журнал"),
    ("/safety", "запреты"),
    ("/mcp", "MCP-мост для ИИ"),
    ("/3d", "3D-ассистент"),
    ("/omni", "обучение Omni"),
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
        if pet3d.first_run_prompt():
            self.refresh_home()
        else:
            pet3d.autostart()
        if self._autostart:
            self.auto_attach()
        while self.running:
            print(ready())
            raw = prompt("  › ")
            line = (raw or "").strip()
            if not line:
                continue
            if pet3d.handle_command(line):
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
        pet_badge = pet3d.badge("ansi")
        pet_on = pet3d.ctl.status == "on"
        print(home_screen(VERSION, game, facts, questions, skills,
                          HOME_COMMANDS, mcp_active=self._mcp_enabled,
                          pet_status=pet_badge, pet_on=pet_on))
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
            "/watch": self.cmd_watch,
            "/learn": self.cmd_learn,
            "/memory": self.cmd_memory,
            "/skills": self.cmd_skills,
            "/journal": self.cmd_journal,
            "/safety": self.cmd_safety,
            "/mcp": self.cmd_mcp,
            "/3d": self.cmd_3d,
            "/3д": self.cmd_3d,
            "/omni": self.cmd_omni,
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
        info("дальше: /status — числа, /watch — наблюдение за твоей игрой")
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
    ("/watch [секунды]", "наблюдение без действий"),
    ("/learn", "одно улучшение"),
                ("/memory [слово]", "факты и статусы"),
                ("/skills", "успех и прирост/мин"),
                ("/journal [n]", "события журнала"),
                ("/safety", "границы действий"),
                ("/mcp", "MCP-мост для ИИ"),
                ("/3d", "3D-ассистент"),
                ("/omni", "обучение Omni"),
                ("/info", "о боте"),
                ("/about", "о проекте"),
                ("/github", "репозиторий"),
                ("/support", "связь"),
                ("/home", "главный экран"),
                ("/theme", "фон вкл/выкл"),
                ("/quit", "выход"),
            ], width - 6),
            "",
            paint("Почему не «просто нажми кнопку»: Алонтито не играет за тебя "
                  "(2026-11-24) — он смотрит на твою игру и подсказывает.",
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
            if rows else "прогонов пока нет — сначала /watch 60",
        ], width=term_width()))
        blank()

        proposal = self.brain.propose()
        if proposal is None:
            info("своих улучшений не предлагаю: Алонтито не играет, ему нечего "
                 "менять в игре. Замеры идут из /watch — твоей игры.")
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

        cycle = self.brain.development()
        active = cycle.get("active")
        lines = [
            f"закрыто экспериментов: {cycle.get('closed', 0)} "
            f"(принято {cycle.get('accepted', 0)}, отклонено {cycle.get('rejected', 0)})",
        ]
        if active:
            lines += [
                f"сейчас: {active.get('change')}",
                f"попыток замера: {active.get('tries', 0)} · последний вердикт: {active.get('last_verdict') or 'ещё нет'}",
                "правило: один эксперимент, пока замер не скажет лучше / хуже / без изменений дважды",
            ]
        else:
            lines.append("открытого эксперимента нет — следующее /learn заведёт его, если есть замер")
        for lesson in cycle.get("lessons") or []:
            lines.append("урок: " + lesson)
        say(box("САМОРАЗВИТИЕ", lines, width=term_width(), color="cyan"))
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

    def cmd_3d(self, args):
        cmd_line = "/3d " + " ".join(args) if args else "/3d"
        pet3d.handle_command(cmd_line)

    def cmd_omni(self, args):
        """Read-only report on the Omni continual-learning layer.

        Reading files and a local SQLite snapshot is the whole action: this
        command never starts the launcher, the dashboard or any game input.
        """
        blank()
        width = term_width()
        st = omni_link.status()
        cfg = st["config"]

        if st["installed"]:
            core = paint(f"{st['core_present']}/{st['core_total']} файлов на месте",
                         "green")
        else:
            core = (paint(f"{st['core_present']}/{st['core_total']} файлов", "yellow")
                    + paint(" — нет: " + ", ".join(st["core_missing"]), "muted"))

        model_line = (paint(str(cfg.get("deep_model", "не задана")), "lilac")
                      + paint(" · fallback ", "muted")
                      + paint(str(cfg.get("fallback_deep_model", "не задана")), "lilac")
                      + paint(" · vision ", "muted")
                      + paint(str(cfg.get("vision_model", "не задана")), "lilac"))

        mem = st["memory"]
        if mem["experiences"] is None:
            memory_line = paint("базы ещё нет — исследователь не запускался", "muted")
        else:
            memory_line = paint(f"{mem['visual_states']} состояний · "
                                f"{mem['experiences']} опытов", "lilac")

        learn = st["learning"]
        if learn["examples"] is None:
            learning_line = paint("базы ещё нет — примеров 0", "muted")
        else:
            learning_line = paint(
                f"{learn['examples']} примеров · {learn['approved']} одобрено · "
                f"{learn['pending']} ждёт экспорта", "lilac")
        curated = ("датасет не создан" if st["curated"] is None
                   else f"{st['curated']} строк в датасете")

        probe = omni_link.ollama_status()
        if probe["state"] == "ok":
            ollama_line = paint(f"отвечает {probe['url']}", "green")
            ollama_line += paint(f" · моделей {len(probe['models'])}", "muted")
            absent = [f"{role}: {name}" for role, name in probe.get("wanted", {}).items()
                      if not probe.get("present", {}).get(role)]
            if absent:
                ollama_line += paint(" · не найдены " + ", ".join(absent), "yellow")
        else:
            ollama_line = (paint(f"не отвечает {probe['url']}", "yellow")
                           + paint(" — локальные модели не запущены", "muted"))

        if learn["eval_runs"] is None:
            eval_line = paint("нет данных", "muted")
        else:
            eval_line = paint(f"{learn['eval_runs']} прогонов · "
                              f"{learn['promotions']} повышений", "lilac")

        lessons = []
        for item in st["lessons"]:
            text = (item.get("lesson") or item.get("outcome") or "").strip()
            text = " ".join(text.split())
            lessons.append(paint("· ", "purple") + clip(text, width - 8))
        if not lessons:
            lessons = [paint("опыта пока нет", "muted")]

        pkgs = st["missing_packages"]
        if pkgs:
            pkg_line = paint(", ".join(name for name, _ in pkgs), "muted")
        else:
            pkg_line = paint("все опциональные пакеты установлены", "green")

        say(box("OMNI · НЕПРЕРЫВНОЕ ОБУЧЕНИЕ", [
            f"ядро      {core}",
            paint("конфиг    ", "muted") + model_line,
            f"Ollama    {ollama_line}",
            paint("память    ", "muted") + memory_line,
            paint("обучение  ", "muted") + learning_line + paint(f" · {curated}", "muted"),
            paint("оценка    ", "muted") + eval_line,
            "",
            paint("последний опыт", "lilac", bold=True),
        ] + lessons + [
            "",
            paint("Режим: только чтение. Терминал не запускает Omni, не открывает "
                  "дашборд и не эмулирует ввод.", "muted"),
            paint("Исследователь UCB1 по умолчанию работает вхолостую: исполнитель "
                  "ввода не подключён, клики по координатам не отправляются.", "muted"),
            "",
            paint("Запуск отдельно:", "lilac", bold=True),
            "  Alontito_Omni.bat   — ядро + дашборд http://127.0.0.1:8080",
            paint("  нужны Ollama и модели из config_omni.json; шаги — "
                  "INTEGRATION_GUIDE.md", "muted"),
            "",
            paint("не хватает: ", "muted") + pkg_line,
        ], width=width, color="purple"))
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
