#!/usr/bin/env python3
"""
Генератор процедурных анимаций для 3D Desktop Pet (alontito/pet3d/animations.json).

Зачем: в hooded_neon_character_rigged.glb нет запечённых клипов (0 animations), только скелет
(Tripo-риг, 41 кость, T-поза). Поэтому клипы описаны данными и «запекаются» в THREE.AnimationClip
прямо в браузерной части (pet.js). Когда в .glb появятся настоящие клипы с теми же именами,
pet.js возьмёт их автоматически, а этот файл станет запасным вариантом.

Система координат модели (проверена по скелету):  вперёд = +X, вверх = +Y, вправо персонажа = +Z.
В T-позе руки лежат вдоль ±Z (R = +Z, L = -Z).

Спецификация позы кости (в каждом ключе):
  {"aim": [x,y,z]}        — длинная ось кости смотрит в МИРОВОМ направлении (абсолютно). Для рук и ног.
  {"rot": [["z",-5],..]}  — поворот ОТНОСИТЕЛЬНО родителя (наследует его наклон). Операции применяются по порядку.
  {"abs": [["z",5],..]}   — абсолютный поворот от позы покоя (например, ступня «плоско на полу»).
  (кость не указана)      — наследует ориентацию родителя.
  "hip": [dx,dy,dz]       — смещение таза в метрах (приседание, подпрыгивание).

Запуск:  python tools/build_pet_clips.py  [путь/к/animations.json]
"""
import json, math, sys
from pathlib import Path

S = math.sin
C = math.cos
rad = math.radians

# ---------- хелперы спецификаций ----------
def A(x, y, z):                       # aim
    n = math.sqrt(x*x + y*y + z*z) or 1.0
    return {"aim": [round(x/n, 4), round(y/n, 4), round(z/n, 4)]}

def R(*ops):                          # относительный поворот
    return {"rot": [[a, round(d, 3)] for a, d in ops]}

def AB(*ops):                         # абсолютный поворот
    return {"abs": [[a, round(d, 3)] for a, d in ops]}

def mirror(d):                        # отражение направления для левой стороны
    return (d[0], d[1], -d[2])

def limb(a_deg, out_deg=0.0):
    """Направление конечности, висящей вниз: a — угол вперёд в сагиттальной плоскости, out — разведение в сторону."""
    a, o = rad(a_deg), rad(out_deg)
    return (C(o)*S(a), -C(o)*C(a), S(o))

def arm(side, up_a, up_o, fo_a, fo_o=None, hand_a=None, hand_o=None):
    """Рука висящего типа. side 'R'|'L'. Углы — как в limb(); локоть = fo (абсолютное направление предплечья)."""
    fo_o = up_o if fo_o is None else fo_o
    hand_a = fo_a if hand_a is None else hand_a
    hand_o = fo_o if hand_o is None else hand_o
    f = (lambda d: d) if side == "R" else mirror
    return {
        f"{side}_Upperarm": A(*f(limb(up_a, up_o))),
        f"{side}_Forearm":  A(*f(limb(fo_a, fo_o))),
        f"{side}_Hand":     A(*f(limb(hand_a, hand_o))),
    }

def arm_dir(side, up, fo, hand=None):
    """Рука по явным векторам (для R — как есть, для L — отражаются)."""
    f = (lambda d: d) if side == "R" else mirror
    hand = fo if hand is None else hand
    return {f"{side}_Upperarm": A(*f(up)), f"{side}_Forearm": A(*f(fo)), f"{side}_Hand": A(*f(hand))}

def arms_sym(up, fo, hand=None):
    d = {}
    d.update(arm_dir("R", up, fo, hand))
    d.update(arm_dir("L", up, fo, hand))
    return d

def leg(side, thigh_a, knee_flex, spread=5.0, toe=0.0):
    """Нога стоя: thigh_a — угол бедра вперёд (°), knee_flex — сгиб колена назад (°)."""
    sgn = 1.0 if side == "R" else -1.0
    zt = sgn * S(rad(spread))
    t = (S(rad(thigh_a)), -C(rad(thigh_a)), zt)
    c = (S(rad(thigh_a - knee_flex)), -C(rad(thigh_a - knee_flex)), zt)
    return {
        f"{side}_Thigh": A(*t),
        f"{side}_Calf": A(*c),
        f"{side}_Foot": AB(("z", toe)),
    }

def legs_sit(spread=0.18, knee_up=0.60):
    d = {}
    for side, sgn in (("R", 1), ("L", -1)):
        d[f"{side}_Thigh"] = A(0.80, knee_up, sgn*spread)
        d[f"{side}_Calf"] = A(0.30, -0.95, sgn*0.10)
        d[f"{side}_Foot"] = AB(("z", 0))
    return d

def merge(*ds):
    out = {}
    for d in ds:
        out.update(d)
    return out

def key(t, bones, hip=(0, 0, 0)):
    return {"t": round(t, 4), "hip": [round(v, 4) for v in hip], "b": bones}

def sample(duration, n, fn):
    """Равномерная выборка периодической функции fn(phase 0..1) -> (bones, hip); последний ключ = первому."""
    keys = []
    for i in range(n + 1):
        ph = (i % n) / n
        bones, hip = fn(ph)
        keys.append(key(duration * i / n, bones, hip))
    return keys

CLIPS = {}

def clip(name, duration, loop, keys, ease="smooth", props=None, **meta):
    CLIPS[name] = {"duration": duration, "loop": loop, "ease": ease, "props": props or [], "meta": meta, "keys": keys}

# =====================================================================
# idle — живая стойка: дыхание грудью, перенос веса с ноги на ногу,
# медленные осмотры с паузами, микродвижение рук. Длинный непериодичный
# цикл (7.2с = два разных «такта»), чтобы не выглядело механическим зацикливанием.
# =====================================================================
def f_idle(p):
    w = 2*math.pi*p
    # перенос веса: медленный крен корпуса + подстраховка бедром
    weight = S(w*0.5)
    breath = S(w)                                   # один дыхательный такт за цикл
    look = S(w*0.5 + 0.9)                           # осмотр в такт переносу веса
    bones = merge(
        {"Spine01": R(("z", -1.2 + 1.6*weight + 0.8*breath), ("y", 2.6*look)),
         "Spine02": R(("z", 0.9*weight + 0.6*S(w + 0.6)), ("y", 1.8*look)),
         "Head": R(("y", 16*look + 4*S(w*0.25)), ("z", 2.8*S(w + 1.0) + 1.5*weight),
                   ("x", 2.0*S(w*0.5) - 1.2*breath)),
         "Waist": R(("z", 1.6*weight))},
        # руки дышат плечами: ключицы чуть поднимаются на вдохе
        {"R_Clavicle": A(0.02*breath, -0.18 + 0.03*breath, 1.0),
         "L_Clavicle": A(0.02*breath, -0.18 + 0.03*breath, -1.0)},
        arm("R", 3 + 2*breath + 1.5*weight, 12 + 2*weight, 9 + 3*breath + 1.0*weight),
        arm("L", 3 + 2*breath - 1.5*weight, 12 - 2*weight, 9 + 3*breath - 1.0*weight),
        leg("R", 1.5 + 1.2*weight, 2.0 + 1.0*weight),
        leg("L", -1.5 + 1.2*weight, 2.0 - 1.0*weight),
    )
    return bones, (0.010*weight, 0.005*breath, 0.008*weight)
clip("idle", 7.2, True, sample(7.2, 36, f_idle))

# =====================================================================
# walk — цикл ходьбы 0.72 с (идёт по +X в модельных координатах; поворот делает pet.js)
# =====================================================================
def f_walk(p):
    w = 2*math.pi*p
    A_THIGH, K = 34.0, 52.0
    def one(side, ph):
        a = A_THIGH*S(ph)
        flex = 6.0 + K*max(0.0, C(ph))**1.4
        toe = -0.45*a if C(ph) < 0 else 14*C(ph)
        return leg(side, a, flex, 4.0, toe)
    bones = merge(
        one("R", w), one("L", w + math.pi),
        arm("R", -0.75*A_THIGH*S(w)*0.8, 10, -0.75*A_THIGH*S(w)*0.8 + 24 + 8*max(0, -S(w)), None),
        arm("L", 0.75*A_THIGH*S(w)*0.8, 10, 0.75*A_THIGH*S(w)*0.8 + 24 + 8*max(0, S(w))),
        {"R_Clavicle": A(0.0, -0.18, 1.0), "L_Clavicle": A(0.0, -0.18, -1.0)},
        {"Spine01": R(("y", 7*S(w)), ("z", -4.0)),
         "Spine02": R(("y", 4*S(w)), ("x", 2.5*S(w))),
         "Head": R(("y", -9*S(w)), ("x", -2.5*S(w)), ("z", 1.5))},
    )
    return bones, (0.0, 0.020*abs(C(w)) - 0.010, 0.012*S(w))
clip("walk", 0.72, True, sample(0.72, 16, f_walk), ease="linear")

# =====================================================================
# sit_idle — сидит на полу, колени вверх, руки на коленях
# =====================================================================
def f_sit(p):
    w = 2*math.pi*p
    bones = merge(
        legs_sit(),
        {"Waist": R(("z", -4.0)),
         "Spine01": R(("z", -5.0 + 1.2*S(w)), ("y", 3*S(w*0.5))),
         "Spine02": R(("z", -3.0 + 0.8*S(w + 0.5))),
         "Head": R(("y", 22*S(w*0.5 + 0.3)), ("z", -4.0 + 3*S(w)), ("x", 3*S(w*0.5)))},
        arm_dir("R", (0.42, -0.86, 0.28), (0.95, -0.30, 0.10)),
        arm_dir("L", (0.42, -0.86, 0.28), (0.95, -0.30, 0.10)),
        {"R_Clavicle": A(0.0, -0.22, 1.0), "L_Clavicle": A(0.0, -0.22, -1.0)},
    )
    return bones, (0.0, -0.225 + 0.004*S(w), 0.0)
clip("sit_idle", 4.0, True, sample(4.0, 16, f_sit))

# =====================================================================
# drag_panic — подвешен за капюшон: болтает ногами, машет руками
# =====================================================================
def f_panic(p):
    w = 2*math.pi*p
    fl = 2*w               # быстрые колебания (2 цикла за клип)
    bones = merge(
        arm_dir("R", (0.10, 0.62 + 0.30*S(fl), 0.75), (0.15, 0.95 + 0.25*S(fl + 1.2), 0.45 + 0.2*S(fl))),
        arm_dir("L", (0.10, 0.62 - 0.30*S(fl), 0.75), (0.15, 0.95 - 0.25*S(fl + 1.2), 0.45 - 0.2*S(fl))),
        leg("R", 34*S(fl), 55 + 30*S(fl + 0.8), 10),
        leg("L", -34*S(fl), 55 - 30*S(fl + 0.8), 10),
        {"R_Clavicle": A(0.0, 0.25, 1.0), "L_Clavicle": A(0.0, 0.25, -1.0),
         "Spine01": R(("x", 8*S(fl)), ("z", 3*S(fl*0.5))),
         "Head": R(("y", 24*S(fl + 0.5)), ("x", -8*S(fl)))},
    )
    return bones, (0.0, 0.07, 0.0)
clip("drag_panic", 0.6, True, sample(0.6, 12, f_panic), ease="linear")

# =====================================================================
# drag_drop — приземление: присел, выпрямился, встряхнулся
# =====================================================================
def stand(sw=0.0, out=12.0, bend=9.0):
    return merge(
        arm("R", sw, out, bend), arm("L", sw, out, bend),
        {"R_Clavicle": A(0.0, -0.18, 1.0), "L_Clavicle": A(0.0, -0.18, -1.0)},
        leg("R", 1.5, 2.0), leg("L", -1.5, 2.0),
    )
crouch = merge(
    arm("R", -10, 55, 5), arm("L", -10, 55, 5),
    {"R_Clavicle": A(0.0, 0.1, 1.0), "L_Clavicle": A(0.0, 0.1, -1.0),
     "Spine01": R(("z", -16.0)), "Head": R(("z", 10.0))},
    leg("R", 42, 82, 12), leg("L", 42, 82, 12),
)
clip("drag_drop", 1.1, False, [
    key(0.00, crouch, (0.0, -0.115, 0.0)),
    key(0.16, merge(crouch, {"Spine01": R(("z", -20.0))}), (0.0, -0.135, 0.0)),
    key(0.42, merge(stand(), {"Spine01": R(("z", 5.0)), "Head": R(("z", -4.0))}), (0.0, 0.018, 0.0)),
    key(0.62, merge(stand(), {"Head": R(("y", 16.0), ("z", 3.0))}), (0.0, 0.0, 0.0)),
    key(0.86, merge(stand(), {"Head": R(("y", -12.0))}), (0.0, 0.0, 0.0)),
    key(1.10, merge(stand(), {"Head": R(("y", 0.0))}), (0.0, 0.0, 0.0)),
])

# =====================================================================
# activity_coding — сидит с ноутбуком, печатает
# =====================================================================
def f_code(p):
    w = 2*math.pi*p
    t1, t2 = 10*w, 10*w + 2.1
    bones = merge(
        legs_sit(0.22, 0.55),
        {"Waist": R(("z", -6.0)),
         "Spine01": R(("z", -9.0 + 1.0*S(w)), ("y", 2*S(w))),
         "Spine02": R(("z", -4.0)),
         "Head": R(("z", -16.0 + 1.5*S(w*2)), ("y", 4*S(w*2 + 0.7)), ("x", 1.0))},
        arm_dir("R", (0.55, -0.80, 0.22), (0.97, -0.10 + 0.08*S(t1), 0.12), (1.0, -0.22 + 0.16*S(t1 + 1), 0.10)),
        arm_dir("L", (0.55, -0.80, 0.22), (0.97, -0.10 + 0.08*S(t2), 0.12), (1.0, -0.22 + 0.16*S(t2 + 1), 0.10)),
        {"R_Clavicle": A(0.0, -0.18, 1.0), "L_Clavicle": A(0.0, -0.18, -1.0)},
    )
    return bones, (0.0, -0.225 + 0.003*S(w*2), 0.0)
clip("activity_coding", 3.0, True, sample(3.0, 30, f_code), ease="linear", props=["laptop"])

# =====================================================================
# activity_rubik — крутит кубик Рубика
# =====================================================================
def f_rubik(p):
    w = 2*math.pi*p
    tw = 2*w   # скручивающие движения
    bones = merge(
        legs_sit(0.24, 0.58),
        {"Waist": R(("z", -4.0)),
         "Spine01": R(("z", -7.0 + 0.8*S(w)), ("y", 3*S(w))),
         "Spine02": R(("z", -3.0)),
         "Head": R(("z", -20.0), ("y", 8*S(w + 1.0)), ("x", 2*S(tw)))},
        arm_dir("R", (0.50, -0.74, 0.34), (0.92, 0.28 + 0.10*S(tw), -0.05 + 0.06*S(tw*1.5)), (0.80, 0.55 + 0.25*S(tw + 1.0), -0.12)),
        arm_dir("L", (0.50, -0.74, 0.34), (0.92, 0.28 - 0.10*S(tw), -0.05 - 0.06*S(tw*1.5)), (0.80, 0.55 - 0.25*S(tw + 1.0), -0.12)),
        {"R_Clavicle": A(0.0, -0.15, 1.0), "L_Clavicle": A(0.0, -0.15, -1.0)},
    )
    return bones, (0.0, -0.225 + 0.003*S(w), 0.0)
clip("activity_rubik", 4.0, True, sample(4.0, 32, f_rubik), ease="linear", props=["rubik"])

# =====================================================================
# portal_reach_in — портал висит сбоку (экранный вправо = -Z персонажа, он открыт к персонажу).
# Персонаж чуть разворачивается к порталу (поворот делает pet.js), тянет ЛЕВУЮ руку в сторону -Z,
# шарит внутри и достаёт предмет (pet.js создаёт предмет в кисти в момент meta.itemAt).
# Векторы руки заданы «как для правой» (z > 0 = наружу): для левой они отражаются автоматически.
# =====================================================================
idle_pose = merge(stand(4, 12, 9), {"Spine01": R(("z", -1.0)), "Head": R()})
def side_reach(upv, fov, lean, head_y=0.0, head_z=0.0, rarm=(3, 12, 9)):
    return merge(
        leg("R", 3, 4, 7), leg("L", -3 - lean*0.1, 4, 7),
        arm("R", *rarm),
        arm_dir("L", upv, fov),
        {"R_Clavicle": A(0.0, -0.18, 1.0), "L_Clavicle": A(0.0, 0.05, -1.0),
         "Waist": R(("x", -lean*0.35)),
         "Spine01": R(("x", -lean*0.65), ("z", -2.0)),
         "Spine02": R(("x", -lean*0.25)),
         "Head": R(("y", head_y), ("z", head_z), ("x", lean*0.5))},
    )
clip("portal_reach_in", 2.7, False, [
    key(0.00, idle_pose, (0, 0, 0)),
    key(0.45, side_reach((0.10, 0.10, 1.0), (0.10, 0.10, 1.0), 6, 26, -3), (0, -0.004, -0.015)),
    key(1.00, side_reach((0.18, 0.14, 1.0), (0.22, 0.14, 1.0), 14, 30, -4), (0, -0.012, -0.040)),
    key(1.25, side_reach((0.18, 0.14, 1.0), (0.14, 0.30, 1.0), 14, 24, -6), (0, -0.012, -0.040)),
    key(1.50, side_reach((0.18, 0.14, 1.0), (0.20, -0.05, 1.0), 14, 34, -2), (0, -0.012, -0.040)),
    key(1.85, side_reach((0.20, -0.40, 0.95), (0.55, 0.35, 0.70), 7, 20, -4), (0, -0.006, -0.020)),
    key(2.30, merge(stand(-4, 12, 9), arm_dir("L", (0.30, -0.70, 0.35), (0.85, 0.40, 0.15)), {"Spine01": R(("z", -2.0)), "Head": R(("z", -10.0), ("y", 8.0))}), (0, 0, 0)),
    key(2.70, merge(stand(-4, 12, 9), arm_dir("L", (0.30, -0.70, 0.35), (0.88, 0.42, 0.12)), {"Spine01": R(("z", -2.0)), "Head": R(("z", -12.0), ("y", 0.0))}), (0, 0, 0)),
], itemAt=1.55, handBone="L_Hand")

# =====================================================================
# portal_eat_drink — предмет у груди → к лицу → «глоток/укус» → довольное покачивание
# (персонаж уже снова лицом к пользователю; предмет в ЛЕВОЙ кисти)
# =====================================================================
def eat(upv, fov, head_z, spine_z, hip=(0, 0, 0), r_sw=3, rbend=9):
    return merge(
        arm("R", r_sw, 12, rbend),
        arm_dir("L", upv, fov),
        {"R_Clavicle": A(0.0, -0.18, 1.0), "L_Clavicle": A(0.0, 0.05, -1.0),
         "Spine01": R(("z", spine_z)), "Head": R(("z", head_z)),
         "R_Thigh": A(*limb(1.5, 5)), "R_Calf": A(*limb(-0.5, 5)), "R_Foot": AB(("z", 0)),
         "L_Thigh": A(*limb(-1.5, -5)), "L_Calf": A(*limb(0.5, -5)), "L_Foot": AB(("z", 0))},
    ), hip
def ek(t, upv, fov, head_z, spine_z, hip=(0, 0, 0), **k):
    b, h = eat(upv, fov, head_z, spine_z, hip, **k)
    return key(t, b, h)
clip("portal_eat_drink", 3.4, False, [
    ek(0.00, (0.30, -0.70, 0.35), (0.88, 0.42, 0.12), -12.0, -2.0),
    ek(0.55, (0.30, -0.30, 0.45), (0.45, 0.88, 0.12), -4.0, -1.0),                 # к лицу
    ek(1.00, (0.25, 0.10, 0.45), (0.30, 0.94, 0.10), 16.0, 4.0, (0, 0.01, 0)),     # запрокинул голову — глоток
    ek(1.35, (0.25, 0.10, 0.45), (0.30, 0.94, 0.10), 20.0, 5.0, (0, 0.012, 0)),
    ek(1.70, (0.30, -0.30, 0.45), (0.50, 0.85, 0.12), -2.0, -1.0),                 # опустил
    ek(2.10, (0.25, 0.10, 0.45), (0.30, 0.94, 0.10), 14.0, 4.0, (0, 0.008, 0)),    # второй глоток
    ek(2.50, (0.35, -0.55, 0.40), (0.75, 0.15, 0.10), -6.0, -3.0, (0, -0.01, 0)),
    ek(2.90, (0.10, -0.97, 0.22), (0.14, -1.0, 0.20), 6.0, 0.0, (0, 0.012, 0), r_sw=2, rbend=9),
    ek(3.40, (0.05, -1.0, 0.28), (0.12, -1.0, 0.20), 0.0, -1.0, (0, 0.0, 0), r_sw=3, rbend=9),
], itemAt=0.0, handBone="L_Hand", vanishAt=2.55)

# =====================================================================
# disintegrate — руки в стороны, лёгкая дрожь, вытягивается вверх (шейдер растворения делает pet.js)
# =====================================================================
def f_dis(p):
    w = 2*math.pi*p
    bones = merge(
        arm_dir("R", (0.0, 0.35, 1.0), (0.0, 0.55, 0.9)),
        arm_dir("L", (0.0, 0.35, 1.0), (0.0, 0.55, 0.9)),
        leg("R", 2, 3, 6), leg("L", -2, 3, 6),
        {"R_Clavicle": A(0.0, 0.2, 1.0), "L_Clavicle": A(0.0, 0.2, -1.0),
         "Spine01": R(("z", 6.0 + 1.5*S(w*6))), "Head": R(("z", 14.0), ("y", 7*S(w*5)))},
    )
    return bones, (0.0, 0.03 + 0.003*S(w*8), 0.0)
dis_keys = [
    key(0.00, merge(stand(4, 12, 9)), (0, 0, 0)),
    key(0.35, f_dis(0.1)[0], f_dis(0.1)[1]),
    key(0.80, f_dis(0.4)[0], f_dis(0.4)[1]),
    key(1.20, f_dis(0.7)[0], f_dis(0.7)[1]),
    key(1.60, f_dis(1.0)[0], f_dis(1.0)[1]),
]
clip("disintegrate", 1.6, False, dis_keys)


# V3 EXTRA CLIPS — stylized procedural animation variations for the current rig.
import copy as _copy
def _v3(name,duration,loop,spine=0,head_y=0,head_z=0,arms=18,knee=5,bounce=0):
    pose=_copy.deepcopy(CLIPS["idle"]["keys"][0]); b=pose["b"]
    b["Spine01"]=R(("z",spine)); b["Spine02"]=R(("z",spine*.35)); b["Head"]=R(("y",head_y),("z",head_z))
    b.update(arm("R",arms,arms+8,arms+10)); b.update(arm("L",arms,arms+8,arms+10))
    b.update(leg("R",2,knee,4)); b.update(leg("L",-2,knee,4))
    mid=_copy.deepcopy(pose); mid["t"]=duration*.5; mid["hip"][1]+=bounce
    mid["b"]["Head"]=R(("y",-head_y*.5),("z",-head_z*.5))
    end=_copy.deepcopy(pose); end["t"]=duration
    clip(name,duration,loop,[pose,mid,end])
_v3("run",.46,True,5,8,0,32,20,.035)
_v3("tiptoe",.75,True,-2,10,0,10,14,.008)
_v3("happy",1.4,False,6,12,0,35,9,.03)
_v3("laugh",1.7,False,10,9,7,28,16,.04)
_v3("excited",1.2,False,8,18,0,45,18,.05)
_v3("sad",2,True,-10,0,13,8,3)
_v3("cry",1.6,False,-12,-8,17,9,5)
_v3("angry",1.25,True,7,-15,-9,20,8)
_v3("annoyed",1.8,True,1,22,-12,14,3)
_v3("surprised",1.15,False,-4,24,0,44,13,.02)
_v3("confused",2,True,-2,24,18,22,3)
_v3("curious",1.8,True,4,28,-12,20,3)
_v3("shy",1.7,False,-8,-15,16,25,7)
_v3("scared",1.1,True,-12,-8,8,42,22)
_v3("proud",1.5,False,10,6,0,20,2)
_v3("facepalm",1.5,False,-3,4,12,38,3)
_v3("shrug",1.3,False,0,8,0,52,3)
_v3("clap",1,True,3,8,0,34,3,.01)
_v3("dance",2.3,True,8,15,7,42,18,.035)
_v3("bow",1.15,False,-22,-5,0,15,5)
_v3("point",1,False,2,12,-5,30,3)
_v3("sneeze",1.15,False,-14,0,15,16,10)
_v3("cough",1.2,False,-12,0,10,14,7)

def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "alontito" / "pet3d" / "animations.json"
    data = {"version": 1, "fps": 30, "driven": [
        "Hip", "Waist", "Spine01", "Spine02", "R_Clavicle", "L_Clavicle",
        "R_Upperarm", "L_Upperarm", "R_Forearm", "L_Forearm", "R_Hand", "L_Hand",
        "Head", "R_Thigh", "L_Thigh", "R_Calf", "L_Calf", "R_Foot", "L_Foot"],
        "clips": CLIPS}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"OK: {len(CLIPS)} clips -> {out} ({out.stat().st_size} bytes): {', '.join(CLIPS)}")

if __name__ == "__main__":
    main()
