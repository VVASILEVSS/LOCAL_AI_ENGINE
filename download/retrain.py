#!/usr/bin/env python3
"""retrain.py — Retrain model on cleaned features_final.csv
Saves model.pkl as sklearn Pipeline (compatible with ollama_client.py ML filter)
"""
import pandas as pd
import numpy as np
import pickle
import os
from sklearn.preprocessing import StandardScaler
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.model_selection import cross_val_score, cross_val_predict, StratifiedKFold
from sklearn.metrics import roc_auc_score, classification_report, accuracy_score

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")

df = pd.read_csv(os.path.join(RESULTS_DIR, "features_final.csv"))
print(f"Dataset: {len(df)} rows")

meta_cols = [c for c in ("symbol", "tf_profile") if c in df.columns]
X = df.drop(columns=meta_cols + ["label_value"]).select_dtypes(include=[np.number]).fillna(0)
y = df["label_value"].astype(int)
print(f"Features: {X.shape[1]}, Labels: {dict(y.value_counts())}")

cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

models = {
    "RandomForest": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", RandomForestClassifier(
            n_estimators=200, max_depth=8, class_weight="balanced",
            random_state=42, n_jobs=-1
        )),
    ]),
    "GradientBoosting": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(
            n_estimators=200, max_depth=4, learning_rate=0.05, random_state=42
        )),
    ]),
    "LogisticRegression": Pipeline([
        ("scaler", StandardScaler()),
        ("clf", LogisticRegression(
            class_weight="balanced", max_iter=1000, random_state=42
        )),
    ]),
}

results = {}
for name, pipe in models.items():
    acc = cross_val_score(pipe, X, y, cv=cv, scoring="accuracy")
    try:
        y_proba = cross_val_predict(pipe, X, y, cv=cv, method="predict_proba")[:, 1]
        auc = roc_auc_score(y, y_proba)
    except Exception:
        auc = None
    results[name] = {"acc_mean": acc.mean(), "acc_std": acc.std(), "auc": auc}
    auc_str = f"{auc:.3f}" if auc else "N/A"
    print(f"  {name}: ACC={acc.mean():.3f}+-{acc.std():.3f}  ROC-AUC={auc_str}")

# Best by AUC (or ACC if no AUC)
best_name = max(results, key=lambda k: results[k]["auc"] if results[k]["auc"] else 0)
print(f"\nBest model: {best_name} (AUC={results[best_name]['auc']:.3f})")

# Train final on ALL data with balanced sample weights
best_pipe = models[best_name]
n_total = len(y)
n_pos = int(y.sum())
n_neg = n_total - n_pos
sw = np.where(y == 1, n_total / (2.0 * n_pos), n_total / (2.0 * n_neg))

if "GradientBoosting" in best_name:
    best_pipe.fit(X, y, clf__sample_weight=sw)
else:
    best_pipe.fit(X, y)

# Save as direct Pipeline (ollama_client.py expects this)
model_path = os.path.join(RESULTS_DIR, "model.pkl")
with open(model_path, "wb") as f:
    pickle.dump(best_pipe, f)

clf_step = best_pipe.steps[-1][1]
feats = list(X.columns)
print(f"\nModel saved: {model_path}")
print(f"Classifier: {type(clf_step).__name__}")
print(f"Features ({len(feats)}):")
for i, feat in enumerate(feats):
    print(f"  {i+1}. {feat}")
