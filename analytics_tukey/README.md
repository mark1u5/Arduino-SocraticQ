# SocraticQ — Comissió Tukey Analytics Cockpit

> **Challenge:** Side Challenge 2 (€200) — Comissió Tukey  
> **Evaluation Criteria:** Utility, Adequacy (Exploratory Data Analysis), Coherence  
> **Author / Owner:** Dev 4 (Pitch Lead & Analytics Architect)

---

## 1. Philosophical Foundation: Why John Tukey?

Standard ed-tech dashboards print simple arithmetic averages (e.g., *"Class Accuracy: 64%, Mean Solve Time: 72s"*). In competitive high-school mathematics (such as Catalonia's **Proves Cangur**), **averages produce dangerous pedagogical decisions**:
- Problem-solving is non-linear and bimodal: a student either grasps the invariant or stalls indefinitely.
- Extreme outliers heavily distort the mean, while medians and five-number summaries remain robust.
- Students frequently experience the **Dunning-Kruger effect**: setting physical confidence dials to 85%+ while repeating structural misconceptions.

Following John W. Tukey’s seminal work (*Exploratory Data Analysis*, 1977), this dashboard provides **visual clarity and resistant statistics** that force educators and autonomous AI agents to notice what averages hide.

---

## 2. Advanced Statistical Formulation

| Metric | Formula | Pedagogical Utility |
| :--- | :--- | :--- |
| **Five-Number Summary** | $\text{Min}, Q_1, \text{Median}, Q_3, \text{Max}$ | Full non-parametric spread of student cognitive load. |
| **Tukey Whiskers** | $[Q_1 - 1.5 \times \text{IQR}, Q_3 + 1.5 \times \text{IQR}]$ | Statistically isolates cognitive stall points without arbitrary time cutoffs. |
| **Tukey's Trimean ($TM$)** | $\frac{Q_1 + 2 \times \text{Median} + Q_3}{4}$ | Outlier-resistant measure of location that incorporates spread weighting. |
| **Quartile Skewness** | $\frac{(Q_3 - \text{Median}) - (\text{Median} - Q_1)}{\text{IQR}}$ | $> 0$ proves an asymmetric right-hand tail of stalled students. |
| **Brier Score** | $\frac{1}{N} \sum_{i=1}^N (c_i - y_i)^2$ | Evaluates probabilistic calibration of the hardware confidence dial ($0..1$). |
| **Cliff's Delta ($\delta$)** | $\frac{\#(x_{\text{pre}} > x_{\text{post}}) - \#(x_{\text{pre}} < x_{\text{post}})}{n_1 \times n_2}$ | Non-parametric effect size quantifying RawTree autonomous agent ROI. |
| **Tukey Residual** | $e_i = y_i - \text{Median}_c(y)$ | Deviation from domain baseline, exposing heteroscedastic stall clusters. |

---

## 3. Architecture & Dual-Mode Telemetry

```text
[ Arduino Uno Q Edge ]          [ RawTree Cloud Workspace ]
         │ (Offline Spool)                 │
         │                                 ▼
         │                         [ JSON-in: telemetry_events ]
         │                                 │
         │                                 ▼ SQL-out
[ Mode A: Local Mock ] <─────────> [ Mode B: Live RawTree ]
 (In-Memory SQLite)                  (REST API Endpoint)
         │                                 │
         └───────────────┬─────────────────┘
                         ▼
             [ analytics_tukey/queries.py ]
                         │
                         ▼
             [ analytics_tukey/charts.py ]
                         │
                         ▼
             [ analytics_tukey/app.py ] (Streamlit Cockpit)
```

- **Mode A (Offline / Local Mock):** Generates 255 verified synthetic Cangur events and 3 agent insights. Zero Wi-Fi required, instant boot.
- **Mode B (Live RawTree):** Sends dynamic SQL queries over HTTP to venue RawTree workspace (`SQL-out`).
- **Stage Resilience:** If venue Wi-Fi drops during judging, the sidebar toggle guarantees zero downtime.

---

## 4. Quickstart

```bash
# 1. Install dependencies
pip install -r analytics_tukey/requirements.txt

# 2. Launch Streamlit Cockpit
python -m streamlit run analytics_tukey/app.py
```

The application will launch on `http://localhost:8501`.
