"""
styles.py — Nyaay AI CSS Theme (Superhuman-inspired)
------------------------------------------------------
Design reference: DESIGN.md

Color palette:
    --mysteria      #1b1938   sidebar / hero background
    --charcoal      #292827   primary text on light surfaces
    --lavender      #cbb7fb   sole accent color
    --amethyst      #714cb6   links
    --cream         #e9e5dd   button background (warm CTA)
    --parchment     #dcd7d3   borders / dividers
    --white         #ffffff   main chat surface
    --white-95      rgba(255,255,255,0.95)  text on dark bg
    --white-60      rgba(255,255,255,0.60)  muted text on dark bg
"""


def get_css() -> str:
    return """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

/* ── Global reset ──────────────────────────────────────────────────────────── */
html, body, [data-testid="stApp"], .stApp {
    font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
    background: #ffffff !important;
}

/* ── Hide Streamlit chrome ──────────────────────────────────────────────────── */
#MainMenu                           { visibility: hidden; }
footer                              { visibility: hidden; }
[data-testid="stToolbar"]           { display: none; }
[data-testid="stDecoration"]        { display: none; }
[data-testid="stStatusWidget"]      { display: none; }
/* Make header transparent but keep its height so the sidebar toggle stays clickable */
[data-testid="stHeader"]            { background: transparent !important; border-bottom: none !important; }
/* Hide native Streamlit sidebar — we use st.columns() for a permanent panel */
[data-testid="stSidebar"],
[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] { display: none !important; }

/* ── Main content area ──────────────────────────────────────────────────────── */
.main .block-container {
    background: #ffffff !important;
    padding-top: 0 !important;
    padding-bottom: 6rem !important;
    max-width: 100% !important;
}

/* ── Left panel — permanent, static, dark purple (st.columns approach) ───────── */

/* Stretch columns to full height */
[data-testid="stHorizontalBlock"] {
    gap: 0 !important;
    align-items: stretch !important;
}

/* Left column */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child {
    background: #1b1938 !important;
    min-height: 100vh !important;
    padding: 1.25rem 0.875rem 2rem !important;
    border-right: 1px solid rgba(203, 183, 251, 0.12) !important;
    overflow-y: auto !important;
    position: relative !important;
}

/* Right column */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child {
    background: #ffffff !important;
    min-height: 100vh !important;
    padding: 0 1.5rem 6rem !important;
    position: relative !important;
}

/* Panel branding */
.nyaay-panel-brand {
    display: flex !important;
    align-items: center !important;
    gap: 0.6rem !important;
    margin-bottom: 1rem !important;
}
.nyaay-panel-logo { font-size: 1.4rem !important; }
.nyaay-panel-name {
    font-size: 1rem !important;
    font-weight: 700 !important;
    color: rgba(255,255,255,0.95) !important;
    letter-spacing: -0.01em !important;
}

/* Section label */
.nyaay-panel-section {
    font-size: 0.68rem !important;
    font-weight: 600 !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
    color: rgba(255,255,255,0.35) !important;
    margin: 0.875rem 0 0.375rem 0.25rem !important;
    padding: 0 !important;
}

/* Empty state */
.nyaay-panel-empty {
    font-size: 0.78rem !important;
    color: rgba(255,255,255,0.3) !important;
    padding: 0.25rem 0.5rem !important;
    margin: 0 !important;
}

/* Active session */
.nyaay-session-active {
    background: rgba(203,183,251,0.15) !important;
    border-radius: 8px !important;
    padding: 5px 9px !important;
    color: #cbb7fb !important;
    font-size: 0.82rem !important;
    margin: 2px 0 !important;
    line-height: 1.4 !important;
}

/* Session list buttons inside left panel */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child .stButton > button {
    background: transparent !important;
    border: none !important;
    color: rgba(255,255,255,0.72) !important;
    text-align: left !important;
    width: 100% !important;
    padding: 0.42rem 0.6rem !important;
    border-radius: 8px !important;
    font-size: 0.82rem !important;
    font-weight: 400 !important;
    line-height: 1.4 !important;
    transition: background 0.12s ease !important;
    white-space: nowrap !important;
    overflow: hidden !important;
    text-overflow: ellipsis !important;
}

[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child .stButton > button:hover {
    background: rgba(203,183,251,0.12) !important;
    color: rgba(255,255,255,0.95) !important;
}

/* New Chat button */
[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child .stButton > button[kind="primary"] {
    background: rgba(203,183,251,0.15) !important;
    border: 1px solid rgba(203,183,251,0.3) !important;
    color: #cbb7fb !important;
    font-weight: 600 !important;
    font-size: 0.85rem !important;
    text-align: center !important;
    margin-bottom: 0.5rem !important;
    padding: 0.5rem 1rem !important;
}

[data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child .stButton > button[kind="primary"]:hover {
    background: rgba(203,183,251,0.25) !important;
}

/* ── Custom branded header ───────────────────────────────────────────────────── */
.nyaay-header {
    display: flex !important;
    align-items: center !important;
    gap: 0.875rem !important;
    padding: 1.25rem 0 1rem !important;
    border-bottom: 1px solid #dcd7d3 !important;
    margin-bottom: 1.25rem !important;
}

.nyaay-logo {
    font-size: 2.25rem !important;
    line-height: 1 !important;
    flex-shrink: 0 !important;
}

.nyaay-header-text h1 {
    font-size: 1.75rem !important;
    font-weight: 700 !important;
    color: #1b1938 !important;
    letter-spacing: -0.03em !important;
    line-height: 1.1 !important;
    margin: 0 0 0.15rem 0 !important;
}

.nyaay-header-text p {
    font-size: 0.85rem !important;
    color: #6b6965 !important;
    margin: 0 !important;
    font-weight: 400 !important;
}

/* ── Typography ─────────────────────────────────────────────────────────────── */
h1 {
    color: #292827 !important;
    font-size: 1.5rem !important;
    font-weight: 600 !important;
    letter-spacing: -0.02em !important;
    line-height: 1.2 !important;
    margin-bottom: 0.1rem !important;
}

h2, h3 {
    color: #292827 !important;
    font-weight: 600 !important;
}

.stMarkdown p, label, .stCaption p {
    color: #292827 !important;
}

.stCaption p, [data-testid="stCaptionContainer"] p {
    color: #6b6965 !important;
    font-size: 0.8rem !important;
}

/* ── Chat messages ──────────────────────────────────────────────────────────── */
[data-testid="stChatMessage"] {
    background: #ffffff !important;
    border: 1px solid #dcd7d3 !important;
    border-radius: 16px !important;
    padding: 1rem 1.25rem !important;
    margin: 0.4rem 0 !important;
    color: #292827 !important;
}

/* User message — warm cream tint */
[data-testid="stChatMessageAvatarUser"] ~ div,
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
    background: #f5f2ed !important;
    border-color: #dcd7d3 !important;
}

[data-testid="stChatMessage"] p,
[data-testid="stChatMessage"] li,
[data-testid="stChatMessage"] span {
    color: #292827 !important;
}

/* Avatar icons */
[data-testid="stChatMessageAvatarUser"],
[data-testid="stChatMessageAvatarAssistant"] {
    background: #1b1938 !important;
    color: #cbb7fb !important;
    border-radius: 8px !important;
}

/* ── Chat input box ──────────────────────────────────────────────────────────── */

/* Container at the bottom of the page — override Streamlit's dark bg */
[data-testid="stBottom"],
[data-testid="stBottom"] > div,
.stBottom,
section[data-testid="stBottom"] {
    background: #ffffff !important;
    border-top: 1px solid #dcd7d3 !important;
    padding: 0.75rem 1rem !important;
}

/* Also target the fixed footer wrapper Streamlit uses */
.st-emotion-cache-h5rgaw,
[class*="st-emotion-cache"] > [data-testid="stBottom"] {
    background: #ffffff !important;
}

/* The input wrapper */
[data-testid="stChatInput"],
[data-testid="stChatInput"] > div,
[data-testid="stChatInput"] div[data-baseweb="base-input"],
[data-testid="stChatInput"] div[data-baseweb="textarea"] {
    background: #ffffff !important;
    border: 1px solid #dcd7d3 !important;
    border-radius: 10px !important;
    box-shadow: 0 1px 6px rgba(27, 25, 56, 0.05) !important;
    outline: none !important;
}

/* Kill ALL red/orange Streamlit focus rings on the input */
[data-testid="stChatInput"] *,
[data-testid="stChatInput"] *:focus,
[data-testid="stChatInput"] *:focus-within,
[data-testid="stChatInput"] *:active {
    outline: none !important;
    box-shadow: none !important;
    border-color: inherit !important;
}

/* Focused state — lavender glow */
[data-testid="stChatInput"]:focus-within,
[data-testid="stChatInput"] div[data-baseweb="textarea"]:focus-within {
    border-color: #cbb7fb !important;
    box-shadow: 0 0 0 3px rgba(203, 183, 251, 0.18) !important;
    outline: none !important;
}

/* The textarea itself */
[data-testid="stChatInput"] textarea {
    background: transparent !important;
    border: none !important;
    outline: none !important;
    box-shadow: none !important;
    color: #292827 !important;
    -webkit-text-fill-color: #292827 !important;
    caret-color: #714cb6 !important;
    font-size: 0.95rem !important;
    font-family: 'Inter', system-ui, sans-serif !important;
    line-height: 1.5 !important;
}

/* ── Main action buttons ─────────────────────────────────────────────────────── */
/* Generate Complaint — dark primary */
.stButton > button[kind="primary"] {
    background: #292827 !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 8px !important;
    font-weight: 600 !important;
    font-size: 0.875rem !important;
    padding: 0.5rem 1.25rem !important;
    transition: opacity 0.15s ease !important;
}

.stButton > button[kind="primary"]:hover {
    opacity: 0.85 !important;
}

/* Secondary / default buttons — warm cream */
.stButton > button:not([kind="primary"]):not(:disabled) {
    background: #e9e5dd !important;
    border: 1px solid #dcd7d3 !important;
    color: #292827 !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
    font-size: 0.875rem !important;
}

.stButton > button:not([kind="primary"]):not(:disabled):hover {
    background: #ddd8d0 !important;
}

/* Disabled */
.stButton > button:disabled {
    background: #f0ede9 !important;
    color: #a8a4a0 !important;
    border: 1px solid #e8e4e0 !important;
    border-radius: 8px !important;
    cursor: not-allowed !important;
}

/* Download buttons */
.stDownloadButton > button {
    background: #e9e5dd !important;
    border: 1px solid #dcd7d3 !important;
    color: #292827 !important;
    border-radius: 8px !important;
    font-weight: 500 !important;
}

.stDownloadButton > button:hover {
    background: #ddd8d0 !important;
}

/* ── Alert / warning boxes ───────────────────────────────────────────────────── */
[data-testid="stAlert"] {
    background: rgba(203, 183, 251, 0.08) !important;
    border-left: 3px solid #cbb7fb !important;
    border-radius: 8px !important;
    color: #292827 !important;
}

[data-testid="stAlert"] p {
    color: #292827 !important;
}

/* ── Text area (draft display) ───────────────────────────────────────────────── */
.stTextArea textarea {
    background: #faf9f7 !important;
    border: 1px solid #dcd7d3 !important;
    border-radius: 8px !important;
    color: #292827 !important;
    -webkit-text-fill-color: #292827 !important;
    opacity: 1 !important;
    font-family: 'Courier New', Courier, monospace !important;
    font-size: 0.85rem !important;
    line-height: 1.6 !important;
}

/* Disabled textarea — Streamlit strips color via -webkit-text-fill-color, restore it */
.stTextArea textarea:disabled,
.stTextArea textarea[disabled] {
    color: #292827 !important;
    -webkit-text-fill-color: #292827 !important;
    opacity: 1 !important;
    cursor: default !important;
}

/* ── Divider ─────────────────────────────────────────────────────────────────── */
hr {
    border-color: #dcd7d3 !important;
    margin: 1.25rem 0 !important;
}

/* ── Spinner ─────────────────────────────────────────────────────────────────── */
[data-testid="stSpinner"] {
    color: #714cb6 !important;
}

/* ── Lavender accent on active session in sidebar ────────────────────────────── */
.active-session > .stButton > button {
    background: rgba(203, 183, 251, 0.15) !important;
    color: #cbb7fb !important;
    font-weight: 500 !important;
}

/* ── Scrollbar ───────────────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; height: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #dcd7d3; border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: #cbb7fb; }

/* ── Skeleton shimmer ────────────────────────────────────────────────────────── */
@keyframes nyaay-shimmer {
    0%   { background-position: -700px 0; }
    100% { background-position: 700px 0; }
}

.skeleton-wrap {
    padding: 0.25rem 0;
}

.skeleton-line {
    height: 13px;
    border-radius: 6px;
    background: linear-gradient(
        90deg,
        #f0ede9 0%,
        #e4e0db 40%,
        #ede9e4 60%,
        #f0ede9 100%
    );
    background-size: 700px 100%;
    animation: nyaay-shimmer 1.5s ease-in-out infinite;
    margin-bottom: 10px;
}

.skeleton-line:last-child { margin-bottom: 0; }

/* ── Lavender accent on active session in sidebar ────────────────────────────── */
.active-session > .stButton > button {
    background: rgba(203, 183, 251, 0.15) !important;
    color: #cbb7fb !important;
    font-weight: 500 !important;
}

/* ── Reset nested columns — prevent sidebar bg bleeding into inner st.columns() ── */
[data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:first-child,
[data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:last-child,
[data-testid="stColumn"] [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] {
    background: transparent !important;
    min-height: auto !important;
    padding: revert !important;
    border-right: none !important;
    overflow-y: visible !important;
    position: static !important;
}
</style>
"""
