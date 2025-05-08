# nutrisnap_app.py
"""
NutriSnap – photo‑based calorie & macro tracker
Streamlit 1.34+   |   Author: you
"""

from __future__ import annotations

import base64, io, json, os, sqlite3
from datetime import date, datetime

import openai
import pandas as pd
import streamlit as st
from PIL import Image
import altair as alt          # 🆕  for the donut chart
import uuid

# compatibility helper -------------------------------------------------
def rerun():
    """Works on old and new Streamlit versions."""
    if hasattr(st, "experimental_rerun"):
        st.experimental_rerun()      # Streamlit ≤ 1.33
    elif hasattr(st, "rerun"):
        st.rerun()                   # Streamlit ≥ 1.34
    else:
        st.warning("Cannot rerun – please upgrade Streamlit.")


# ───────────────────────────── CONFIG ─────────────────────────────
st.set_page_config(
    page_title="NutriSnap",
    page_icon="🍽️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# set your key  ➜  export OPENAI_API_KEY="sk‑proj‑…"
openai.api_key = os.getenv("OPENAI_API_KEY")

DB = "nutrition_log.db"

ACTIVITY = {
    "Sedentary": 1.2,
    "Light (1‑2×/wk)": 1.375,
    "Moderate (3‑5×/wk)": 1.55,
    "Heavy (6‑7×/wk)": 1.725,
    "Athlete / Physical job": 1.9,
}
GOALS = {
    "Lose Weight (‑20 %)": 0.8,
    "Maintain": 1.0,
    "Gain Muscle (+15 %)": 1.15,
}

# ───────────────────────────── DB ─────────────────────────────
def init_db():
    with sqlite3.connect(DB) as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS meals(
                id INTEGER PRIMARY KEY,
                log_date TEXT,
                meal_name TEXT,
                ts TEXT,
                serving TEXT,
                calories REAL,
                protein_g REAL,
                carbs_g REAL,
                fat_g REAL,
                fiber_g REAL,
                sugar_g REAL
            )"""
        )
        # auto‑add missing columns if we evolve schema
        cols = {r[1] for r in c.execute("PRAGMA table_info(meals)")}
        expected = {
            "calories",
            "protein_g",
            "carbs_g",
            "fat_g",
            "fiber_g",
            "sugar_g",
            "serving",
            "ts",
        }
        for col in expected - cols:
            c.execute(f"ALTER TABLE meals ADD COLUMN {col} REAL;")


def log_meal(name: str, serving: str, n: dict):
    with sqlite3.connect(DB) as c:
        c.execute(
            """INSERT INTO meals
            (log_date, meal_name, ts, serving, calories, protein_g, carbs_g, fat_g,
             fiber_g, sugar_g)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                str(date.today()),
                name,
                datetime.now().strftime("%H:%M"),
                serving,
                n["calories"],
                n["protein_g"],
                n["carbs_g"],
                n["fat_g"],
                n.get("fiber_g", 0),
                n.get("sugar_g", 0),
            ),
        )


def today_df() -> pd.DataFrame:
    with sqlite3.connect(DB) as c:
        return pd.read_sql(
            "SELECT id, meal_name, ts, serving, calories, protein_g, carbs_g, fat_g "
            "FROM meals WHERE log_date = ?",
            c,
            params=[str(date.today())],
        )


def delete_meal(row_id: int):
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM meals WHERE id = ?", (row_id,))


# ──────────────────────── Nutrition, OpenAI ───────────────────────
def mifflin(sex: str, kg: float, cm: float, age: int) -> float:
    bmr = 10 * kg + 6.25 * cm - 5 * age
    return bmr + (5 if sex == "Male" else -161)


@st.cache_resource(show_spinner=False)
# ───────────────────────── GPT‑4o Vision helper ─────────────────────────
def vision_estimate(image_bytes: bytes, user_label: str | None = None) -> dict:
    """
    Returns dict with keys:
      calories, protein_g, carbs_g, fat_g, fiber_g, sugar_g,
      serving, meal_name
    If user_label is provided, GPT is asked to confirm or correct it.
    """
    uri = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()

    if user_label:
        system_msg = (
            "You are a nutritionist. The user says the meal is "
            f"'{user_label}'. Confirm or correct that description, then "
            "estimate nutrition. Respond ONLY as JSON with keys: meal_name "
            "(a short human‑readable name), calories, protein_g, carbs_g, "
            "fat_g, fiber_g, sugar_g, serving."
        )
    else:
        system_msg = (
            "Identify this meal and estimate its nutrition. "
            "Respond ONLY as JSON with keys: meal_name, calories, protein_g, "
            "carbs_g, fat_g, fiber_g, sugar_g, serving."
        )

    resp = openai.chat.completions.create(
        model="gpt-4o-mini",
        response_format={"type": "json_object"},
        max_tokens=350,
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": system_msg},
                {"type": "image_url", "image_url": {"url": uri}}
            ]}
        ],
    )
    return json.loads(resp.choices[0].message.content)



# ───────────────────────────── UI utils ───────────────────────────
CSS = """
<style>
/* Use Streamlit’s theme variables so it auto‑flips for light / dark */
:root {
  /* variables provided by Streamlit 1.20+ */
  --bg: var(--background-color);
  --card: var(--secondary-background-color);
  --text: var(--text-color);
  --muted: var(--text-color-subdued);
  --primary: var(--primary-color);      /* honours theme.primaryColor */
}

.stApp {background: var(--bg);}

/* ▶︎ Generic card --------------------------------------------- */
.card {
  background: var(--card);
  border-radius: 12px;
  padding: 26px 32px;
  margin-bottom: 24px;
  box-shadow: 0 1px 3px rgb(0 0 0 / 8%);
  color: var(--text);
}
.metric {font: 700 32px/1 var(--font); color: var(--text);}
.label  {font: 400 14px/1.4 var(--font); color: var(--muted); margin-top: 4px;}

/* ▶︎ Buttons (Streamlit adds .stButton) ----------------------- */
button[kind="primary"], .btn {
  background: var(--primary) !important;
  color: #fff !important;
  border: none !important;
  border-radius: 6px !important;
  font-weight: 600 !important;
}
button[kind="primary"]:hover { filter: brightness(.95); }

/* ▶︎ Progress bars (use fixed brand colours – still readable) -- */
.progress-wrap {height: 10px; background: #e0e0e0; border-radius: 6px;}
.progress    {height: 100%; border-radius: 6px;}
.protein {background: #4f7fff;}
.carbs   {background: #4caf50;}
.fat     {background: #ffbe21;}

/* ▶︎ Meals table ---------------------------------------------- */
table.meals {width: 100%; border-collapse: collapse; font-size: 15px; color: var(--text);}
table.meals td {padding: 6px 4px; border-bottom: 1px solid var(--card);}
.remove {color: #e03535; cursor: pointer; font-size: 14px;}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


# ───────── Session state & initial view ────────
if "profile" not in st.session_state:
    st.session_state.profile = None          # forces profile first time
if "view" not in st.session_state:
    st.session_state.view = "profile" if st.session_state.profile is None else "dashboard"

# ───────── Top nav (hidden until profile complete) ─────────
def render_nav():
    nav = st.radio(
        "Navigation",
        ["Dashboard", "Add Meal", "Profile"],
        horizontal=True,
        label_visibility="collapsed",
        index=["dashboard", "add", "profile"].index(st.session_state.view),
    )
    st.session_state.view = (
        "dashboard" if nav == "Dashboard"
        else "add" if nav == "Add Meal"
        else "profile"
    )

if st.session_state.profile:
    render_nav()


# ─────────────────────────── PROFILE EDIT ─────────────────────────
def profile_editor():
    st.header("Edit Profile")
    sex = st.radio("Sex", ["Male", "Female"], horizontal=True)
    age = st.number_input("Age", 10, 100, value=30, step=1)
    wt = st.number_input("Weight (kg)", 30.0, 250.0, value=70.0)
    ht = st.number_input("Height (cm)", 120.0, 250.0, value=175.0)
    act = st.selectbox("Activity level", list(ACTIVITY))
    goal = st.selectbox("Goal", list(GOALS))
    if st.button("Save", type="primary"):
        st.session_state.profile = {
            "sex": sex, "age": age, "wt": wt,
            "ht": ht, "act": act, "goal": goal,
        }
        st.success("Profile saved!")
        # jump straight to dashboard
        st.session_state.view = "dashboard"
        st.rerun()             # or rerun() helper if using older Streamlit

# Beautify calorie tracker
def calorie_donut(consumed: float, target: float):
    remaining = max(target - consumed, 0)
    base = pd.DataFrame({"kcal": [consumed, remaining],
                         "label": ["Consumed", "Remaining"]})
    chart = (
        alt.Chart(base)
        .mark_arc(outerRadius=100, innerRadius=60)
        .encode(
            theta="kcal",
            color=alt.Color("label",
                scale=alt.Scale(
                    domain=["Consumed", "Remaining"],
                    range=["#4caf50", "#e0e0e0"],
                ),
                legend=None,
            ),
        )
    )
    st.altair_chart(chart, use_container_width=True)


# ─────────────────────────── DASHBOARD ────────────────────────────
def dashboard():
    # greeting & edit link
    name = "Vasu Sharma"
    col1, col2 = st.columns([8, 2])
    with col1:
        st.write(f"## Hello, {name}")
    with col2:
        if st.button("Edit Profile"):
            st.session_state.view = "profile"
            rerun()

    # ensure profile exists
    if not st.session_state.profile:
        st.info("Complete your profile first.")
        st.session_state.view = "profile"
        rerun()

    p   = st.session_state.profile
    bmr = mifflin(p["sex"], p["wt"], p["ht"], p["age"])
    tdee = bmr * ACTIVITY[p["act"]] * GOALS[p["goal"]]

    target = tdee         

    df  = today_df()
    cal = df["calories"].sum()

    # ▸ Daily‑target card
    st.markdown(
        f"<div class='card' style='text-align:center'>"
        f"<div class='metric'>{target:.0f} kcal</div>"
        f"<div class='label'>Daily Target</div></div>",
        unsafe_allow_html=True,
    )

    # ▸ Donut showing consumed vs. remaining
    calorie_donut(cal, target)

    # ▸ Consumed card
    st.markdown(
        f"<div class='card' style='text-align:center;margin-top:16px'>"
        f"<div class='metric'>{cal:.0f} kcal</div>"
        f"<div class='label'>Consumed today</div></div>",
        unsafe_allow_html=True,
    )

    # ▸ Macro bars
    st.write("### Macronutrients")
    macro_targets = {
        "protein_g": 0.25 * target / 4,
        "carbs_g":   0.50 * target / 4,
        "fat_g":     0.25 * target / 9,
    }
    for key, css in zip(["protein_g","carbs_g","fat_g"], ["protein","carbs","fat"]):
        val, cap = df[key].sum(), macro_targets[key]
        pct = min(val / cap, 1)
        st.markdown(
            f"<div style='margin:4px 0 12px'>"
            f"<span style='font-weight:600'>{key.replace('_g','').title()} "
            f"{val:.0f}/{cap:.0f} g</span>"
            f"<div class='progress-wrap'><div class='progress {css}' style='width:{pct*100:.1f}%'></div></div>"
            f"</div>",
            unsafe_allow_html=True,
        )

    # ── Today's Meals table ──────────────────────────────────────────────
    st.write("### Today’s Meals")

    if df.empty:
        st.info("No meals logged yet.")
    else:
        # Header row
        hdr = st.columns([3, 1, 1, 1, 1, 1, 0.4])  # widths must match data rows
        hdr[0].markdown("**Meal**")
        hdr[1].markdown("**Calories**")
        hdr[2].markdown("**Protein (g)**")
        hdr[3].markdown("**Carbs (g)**")
        hdr[4].markdown("**Fat (g)**")
        hdr[5].markdown("**Time**")
        hdr[6].markdown(" ")  # spacer for delete icon

        # Data rows
        for _, r in df.iterrows():
            cols = st.columns([3, 1, 1, 1, 1, 1, 0.4])
            cols[0].markdown(f"**{r.meal_name}**  \n<small>{r.serving or ''}</small>", unsafe_allow_html=True)
            cols[1].markdown(f"{r.calories:.0f}")
            cols[2].markdown(f"{r.protein_g:.0f}")
            cols[3].markdown(f"{r.carbs_g:.0f}")
            cols[4].markdown(f"{r.fat_g:.0f}")
            cols[5].markdown(r.ts)
            if cols[6].button("✖", key=f"del-{r.id}", help=f"Delete {r.meal_name}"):
                delete_meal(int(r.id))
                st.success(f"Deleted **{r.meal_name}**")
                rerun()


                

    # # today's meals table
    # st.write("### Today's Meals")
    # if df.empty:
    #     st.info("No meals logged yet.")
    # else:
    #     # build HTML table to include remove links
    #     rows = ""
    #     for _, r in df.iterrows():
    #         rows += (
    #             f"<tr>"
    #             f"<td>{r.meal_name}</td>"
    #             f"<td>{r.ts}</td>"
    #             f"<td>{r.serving or ''}</td>"
    #             f"<td>{r.calories:.0f} kcal</td>"
    #             f"<td class='remove' "
    #             f"onClick=\"window.location.href='?del={r.id}'\">Remove</td>"
    #             f"</tr>"
    #         )
    #     st.markdown(
    #         "<table class='meals'><tbody>" + rows + "</tbody></table>",
    #         unsafe_allow_html=True,
    #     )


# ─────────────────────────── ADD MEAL ─────────────────────────────
def add_meal():
    st.write("### Add Meal")
    st.markdown("<a class='link' href='?back=1'>Back to Dashboard</a>", unsafe_allow_html=True)

    up = st.file_uploader("Upload Food Image", ["jpg", "jpeg", "png"])
    if up:
        img = Image.open(up).convert("RGB")



    name_in = st.text_input("Meal description (optional)")  #  ← label updated

    if up and st.button("Analyze", key="analyze"):
        with st.spinner("Running AI vision…"):
            buf = io.BytesIO(); Image.open(up).convert("RGB").save(buf, format="JPEG")
            info = vision_estimate(buf.getvalue(), user_label=name_in or None)
        st.session_state.analysis = info     # keep result in memory
        st.success("Analysis complete!")


    info = st.session_state.get("analysis")   # returns None until Analyse runs

    if info:
        # 1) captioned preview
        if up:  # img already loaded earlier
            st.image(
                img,
                caption=f"{info.get('meal_name', name_in or 'Meal')} – "
                        f"{info['calories']:.0f} kcal",
                use_column_width=True,
            )

        # 2) nutrition facts
        st.markdown("#### Nutrition Information")
        left, right = st.columns(2)
        left.metric("Calories", f"{info['calories']:.0f} kcal")
        right.metric("Serving", info.get("serving", "1 serving"))

        grid = {
            "Protein": f"{info['protein_g']:.1f} g",
            "Carbs":   f"{info['carbs_g']:.1f} g",
            "Fat":     f"{info['fat_g']:.1f} g",
            "Fiber":   f"{info.get('fiber_g',0):.1f} g",
            "Sugar":   f"{info.get('sugar_g',0):.1f} g",
        }
        for k, v in grid.items():
            st.markdown(f"**{k}** {v}")

        # 3) save button
        if st.button("Add to My Meals", type="primary"):
            final_name = name_in.strip() or info.get("meal_name", "Untitled meal")
            log_meal(final_name, info.get("serving", "1 serving"), info)
            st.success(f"Saved: **{final_name}**")
            st.session_state.pop("analysis")        # clear for next upload
            st.session_state.view = "dashboard"
            st.rerun()



# # ───── navigation (hide until profile is set) ─────
# if st.session_state.profile:
#     nav = st.radio(
#         "Navigation",
#         ["Dashboard", "Add Meal", "Profile"],
#         horizontal=True,
#         label_visibility="collapsed",
#     )
#     st.session_state.view = (
#         "dashboard" if nav == "Dashboard"
#         else "add" if nav == "Add Meal"
#         else "profile"
#     )


# ─────────────────────────── ROUTER ───────────────────────────────
init_db()

# handle query actions (remove row or nav)
# qs = st.experimental_get_query_params()
qs = st.query_params  
# if "del" in qs:
#     delete_meal(int(qs["del"][0]))
#     st.experimental_set_query_params()  # clear params
#     rerun()
if "back" in qs:
    st.experimental_set_query_params()
    st.query_params.clear()       # wipes everything
    st.query_params.update(page="dashboard")

# main view switch
match st.session_state.view:
    case "dashboard":
        dashboard()
    case "add":
        add_meal()
    case "profile":
        profile_editor()
