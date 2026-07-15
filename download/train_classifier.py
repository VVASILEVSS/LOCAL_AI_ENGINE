#!/usr/bin/env python3
"""
train_classifier.py v1
=======================
Train ML classifier on extracted features.

Uses features.csv from extract_features.py.
Trains multiple models, compares via cross-validation, saves best model.

Output:
  results/model.pkl          — trained model
  results/model_metrics.json  — evaluation metrics
  results/feature_importance.json — feature importance ranking

Usage:
  python tools/train_classifier.py
  python tools/train_classifier.py --input results/features.csv
  python tools/train_classifier.py --cv-folds 5 --min-samples 50
"""

import argparse
import csv
import json
import math
import os
import random
import sys
import time
from collections import defaultdict
from typing import List, Dict, Tuple, Optional

# ─── Check available ML libraries ───────────────────────────────────────
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold, cross_val_score, cross_val_predict
    from sklearn.metrics import (
        accuracy_score, precision_score, recall_score, f1_score,
        roc_auc_score, confusion_matrix, classification_report
    )
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline
    HAS_SKLEARN = True
except ImportError:
    HAS_SKLEARN = False

try:
    import pickle
    HAS_PICKLE = True
except ImportError:
    HAS_PICKLE = False


# ============================================================================
# DATA LOADING
# ============================================================================

def load_features_csv(path: str) -> Tuple[List[dict], List[str]]:
    """Load features.csv, return (rows, feature_columns)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # Identify feature columns (exclude metadata and target)
    skip_cols = {
        "row_idx", "symbol", "tf_profile", "candidate_type",
        "mfe_pct", "mae_pct", "label_value",
    }
    feature_cols = [c for c in fieldnames if c not in skip_cols and c.strip()]

    return rows, feature_cols


def to_numpy(rows: List[dict], feature_cols: List[str], label_col: str = "label_value"):
    """Convert rows to X (numpy array) and y (numpy array)."""
    n = len(rows)
    n_features = len(feature_cols)

    X = []
    y = []
    valid_rows = []

    for i, row in enumerate(rows):
        try:
            features = []
            for col in feature_cols:
                val = row.get(col, "0")
                features.append(float(val) if val and val != "nan" else 0.0)
            label = int(float(row.get(label_col, "0")))
            X.append(features)
            y.append(label)
            valid_rows.append(row)
        except (ValueError, TypeError):
            continue

    if HAS_NUMPY:
        X = np.array(X, dtype=float)
        y = np.array(y, dtype=int)
    else:
        X = X  # list of lists
        y = y  # list

    return X, y, valid_rows


# ============================================================================
# MODEL TRAINING (Pure Python fallback)
# ============================================================================

class SimpleDecisionStump:
    """Single-feature decision stump as baseline."""

    def __init__(self):
        self.feature_idx = 0
        self.threshold = 0.0
        self.direction = 1  # 1 = above threshold -> positive

    def fit(self, X, y):
        best_acc = 0
        n_features = len(X[0]) if len(X) > 0 else 0

        for fi in range(n_features):
            vals = sorted(set(r[fi] for r in X))
            for vi in range(1, len(vals)):
                thresh = (vals[vi-1] + vals[vi]) / 2
                for direction in [1, -1]:
                    preds = []
                    for row in X:
                        if direction == 1:
                            preds.append(1 if row[fi] >= thresh else 0)
                        else:
                            preds.append(1 if row[fi] < thresh else 0)

                    correct = sum(1 for p, t in zip(preds, y) if p == t)
                    acc = correct / len(y)

                    if acc > best_acc:
                        best_acc = acc
                        self.feature_idx = fi
                        self.threshold = thresh
                        self.direction = direction

        return self

    def predict(self, X):
        preds = []
        for row in X:
            if self.direction == 1:
                preds.append(1 if row[self.feature_idx] >= self.threshold else 0)
            else:
                preds.append(1 if row[self.feature_idx] < self.threshold else 0)
        return preds


class PureRandomForest:
    """Simplified Random Forest (pure Python, no dependencies)."""

    def __init__(self, n_trees=10, max_depth=5, min_samples_split=5, random_state=42):
        self.n_trees = n_trees
        self.max_depth = max_depth
        self.min_samples_split = min_samples_split
        self.random_state = random_state
        self.trees = []
        self.feature_importances = None

    def fit(self, X, y):
        rng = random.Random(self.random_state)
        n_features = len(X[0]) if len(X) > 0 else 0
        self.trees = []

        for _ in range(self.n_trees):
            # Bootstrap sample
            indices = [rng.randint(0, len(X) - 1) for _ in range(len(X))]
            X_boot = [X[i] for i in indices]
            y_boot = [y[i] for i in indices]

            # Random feature subset
            n_feat_subset = max(3, int(math.sqrt(n_features)))
            feat_indices = sorted(rng.sample(range(n_features), n_feat_subset))

            tree = self._build_tree(X_boot, y_boot, feat_indices, depth=0, rng=rng)
            self.trees.append((tree, feat_indices))

        # Feature importance (simplified: count of splits per feature)
        feat_counts = defaultdict(int)
        for tree, feat_idx in self.trees:
            self._count_features(tree, feat_counts)
        total = sum(feat_counts.values()) or 1
        self.feature_importances = {f: c / total for f, c in feat_counts.items()}

        return self

    def _build_tree(self, X, y, feat_indices, depth, rng):
        n = len(X)
        if n < self.min_samples_split or depth >= self.max_depth:
            n_pos = sum(y)
            n_neg = n - n_pos
            return {"leaf": True, "prob": n_pos / n if n > 0 else 0.5}

        # Find best split
        best_gini = 1.0
        best_feature = None
        best_threshold = None
        best_left = None
        best_right = None

        for fi in feat_indices:
            vals = sorted(set(r[fi] for r in X))
            if len(vals) < 2:
                continue

            # Sample thresholds
            step = max(1, len(vals) // 10)
            sampled = vals[::step]

            for thresh in sampled:
                left_X, left_y = [], []
                right_X, right_y = [], []
                for i in range(n):
                    if X[i][fi] <= thresh:
                        left_X.append(X[i])
                        left_y.append(y[i])
                    else:
                        right_X.append(X[i])
                        right_y.append(y[i])

                if len(left_y) < 2 or len(right_y) < 2:
                    continue

                gini = self._gini(left_y) * len(left_y) / n + self._gini(right_y) * len(right_y) / n
                if gini < best_gini:
                    best_gini = gini
                    best_feature = fi
                    best_threshold = thresh
                    best_left = (left_X, left_y)
                    best_right = (right_X, right_y)

        if best_feature is None:
            n_pos = sum(y)
            return {"leaf": True, "prob": n_pos / n if n > 0 else 0.5}

        left_tree = self._build_tree(best_left[0], best_left[1], feat_indices, depth + 1, rng)
        right_tree = self._build_tree(best_right[0], best_right[1], feat_indices, depth + 1, rng)

        return {
            "leaf": False,
            "feature": best_feature,
            "threshold": best_threshold,
            "left": left_tree,
            "right": right_tree,
        }

    def _gini(self, y):
        n = len(y)
        if n == 0:
            return 0
        n_pos = sum(y)
        n_neg = n - n_pos
        p_pos = n_pos / n
        p_neg = n_neg / n
        return 1.0 - p_pos ** 2 - p_neg ** 2

    def _count_features(self, node, counts):
        if node.get("leaf"):
            return
        counts[node["feature"]] += 1
        self._count_features(node["left"], counts)
        self._count_features(node["right"], counts)

    def predict_proba(self, X):
        results = []
        for row in X:
            probs = []
            for tree, feat_indices in self.trees:
                node = tree
                while not node.get("leaf"):
                    fi = node["feature"]
                    if fi in range(len(row)):
                        if row[fi] <= node["threshold"]:
                            node = node["left"]
                        else:
                            node = node["right"]
                    else:
                        node = node["left"]
                probs.append(node["prob"])
            avg_prob = sum(probs) / len(probs) if probs else 0.5
            results.append(avg_prob)
        return results

    def predict(self, X):
        return [1 if p >= 0.5 else 0 for p in self.predict_proba(X)]


# ============================================================================
# EVALUATION
# ============================================================================

def evaluate(y_true, y_pred, y_proba=None):
    """Compute evaluation metrics."""
    n = len(y_true)
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    accuracy = correct / n if n > 0 else 0

    tp = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 1)
    fp = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 1)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t == 1 and p == 0)
    tn = sum(1 for t, p in zip(y_true, y_pred) if t == 0 and p == 0)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

    metrics = {
        "accuracy": round(accuracy, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "n": n,
    }

    if y_proba is not None:
        # Simple AUC approximation
        try:
            from sklearn.metrics import roc_auc_score
            metrics["roc_auc"] = round(roc_auc_score(y_true, y_proba), 4)
        except Exception:
            metrics["roc_auc"] = None

    return metrics


def stratified_kfold_split(indices, labels, n_folds=5, seed=42):
    """Simple stratified k-fold split (pure Python)."""
    rng = random.Random(seed)

    # Group indices by label
    by_label = defaultdict(list)
    for idx in indices:
        by_label[labels[idx]].append(idx)

    # Shuffle within each group
    for label in by_label:
        rng.shuffle(by_label[label])

    # Distribute to folds
    folds = [[] for _ in range(n_folds)]
    for label in sorted(by_label.keys()):
        items = by_label[label]
        for i, item in enumerate(items):
            folds[i % n_folds].append(item)

    # Create train/test splits
    splits = []
    for i in range(n_folds):
        test_idx = set(folds[i])
        train_idx = set()
        for j in range(n_folds):
            if j != i:
                train_idx.update(folds[j])
        splits.append((sorted(train_idx), sorted(test_idx)))

    return splits


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="Train ML classifier on features")
    parser.add_argument("--input", default="results/features.csv", help="Features CSV")
    parser.add_argument("--output-dir", default="results", help="Output directory for model")
    parser.add_argument("--cv-folds", type=int, default=5, help="Number of CV folds")
    parser.add_argument("--min-samples", type=int, default=30, help="Minimum samples to train")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--no-sklearn", action="store_true", help="Force pure Python mode")
    args = parser.parse_args()

    # Check features file
    if not os.path.exists(args.input):
        print(f"ERROR: {args.input} not found. Run extract_features.py first!")
        sys.exit(1)

    print(f"Reading: {args.input}")
    rows, feature_cols = load_features_csv(args.input)
    print(f"Rows: {len(rows)}, Features: {len(feature_cols)}")

    # Convert to arrays
    X, y, valid_rows = to_numpy(rows, feature_cols)

    n_total = len(y)
    n_pos = int(sum(y))
    n_neg = n_total - n_pos
    print(f"Labels: {n_pos} positive (win), {n_neg} negative (loss)")

    if n_total < args.min_samples:
        print(f"ERROR: Only {n_total} samples (minimum {args.min_samples} required).")
        sys.exit(1)

    # Check library availability
    use_sklearn = HAS_SKLEARN and HAS_NUMPY and not args.no_sklearn
    print(f"Mode: {'scikit-learn' if use_sklearn else 'pure Python (fallback)'}")

    if not use_sklearn and not HAS_NUMPY:
        print("WARNING: numpy not available, using pure Python. Install numpy+sklearn for better results.")

    # ─── Cross-Validation ────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  {args.cv_folds}-FOLD CROSS-VALIDATION")
    print(f"{'='*60}")

    indices = list(range(n_total))

    # ================================================================
    # MODEL 1: Decision Stump (baseline)
    # ================================================================
    print("\n--- Model 1: Decision Stump (baseline) ---")
    stump_metrics_list = []
    for fold_idx, (train_idx, test_idx) in enumerate(stratified_kfold_split(indices, y, args.cv_folds, args.seed)):
        X_train = [X[i] for i in train_idx]
        y_train = [y[i] for i in train_idx]
        X_test = [X[i] for i in test_idx]
        y_test = [y[i] for i in test_idx]

        stump = SimpleDecisionStump()
        stump.fit(X_train, y_train)
        preds = stump.predict(X_test)

        m = evaluate(y_test, preds)
        stump_metrics_list.append(m)
        print(f"  Fold {fold_idx+1}: ACC={m['accuracy']:.3f} F1={m['f1']:.3f}")

    avg_stump = {k: sum(m[k] for m in stump_metrics_list) / len(stump_metrics_list)
                 for k in ["accuracy", "precision", "recall", "f1"]}

    # ================================================================
    # MODEL 2: Random Forest (pure Python)
    # ================================================================
    print("\n--- Model 2: Random Forest (pure Python, 20 trees) ---")
    rf_metrics_list = []
    rf_models = []
    for fold_idx, (train_idx, test_idx) in enumerate(stratified_kfold_split(indices, y, args.cv_folds, args.seed)):
        X_train = [X[i] for i in train_idx]
        y_train = [y[i] for i in train_idx]
        X_test = [X[i] for i in test_idx]
        y_test = [y[i] for i in test_idx]

        rf = PureRandomForest(n_trees=20, max_depth=4, min_samples_split=5,
                              random_state=args.seed + fold_idx)
        rf.fit(X_train, y_train)
        preds = rf.predict(X_test)
        rf_models.append(rf)

        m = evaluate(y_test, preds)
        rf_metrics_list.append(m)
        print(f"  Fold {fold_idx+1}: ACC={m['accuracy']:.3f} PREC={m['precision']:.3f} "
              f"REC={m['recall']:.3f} F1={m['f1']:.3f}")

    avg_rf = {k: sum(m[k] for m in rf_metrics_list) / len(rf_metrics_list)
              for k in ["accuracy", "precision", "recall", "f1"]}

    # ================================================================
    # MODEL 3: sklearn (if available)
    # ================================================================
    avg_sklearn_rf = None
    avg_sklearn_gb = None
    avg_sklearn_lr = None

    if use_sklearn:
        skf = StratifiedKFold(n_splits=args.cv_folds, shuffle=True, random_state=args.seed)

        # RF
        print("\n--- Model 3: sklearn RandomForest (100 trees) ---")
        rf_sk = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", RandomForestClassifier(
                n_estimators=100, max_depth=6, min_samples_split=5,
                random_state=args.seed, class_weight="balanced"
            )),
        ])
        cv_scores = cross_val_score(rf_sk, X, y, cv=skf, scoring="accuracy")
        print(f"  CV Accuracy: {cv_scores.mean():.3f} (+/- {cv_scores.std()*2:.3f})")

        # Per-fold details
        y_pred_cv = cross_val_predict(rf_sk, X, y, cv=skf)
        m_rf_sk = evaluate(y, y_pred_cv, None)
        avg_sklearn_rf = {k: m_rf_sk[k] for k in ["accuracy", "precision", "recall", "f1", "roc_auc"] if k in m_rf_sk}
        print(f"  Overall: ACC={m_rf_sk['accuracy']:.3f} F1={m_rf_sk['f1']:.3f}"
              f" ROC-AUC={m_rf_sk.get('roc_auc', 'N/A')}")

        # Feature importance
        rf_sk.fit(X, y)
        importances = rf_sk.named_steps["clf"].feature_importances_
        feat_imp = sorted(zip(feature_cols, importances), key=lambda x: x[1], reverse=True)

        print(f"\n  Top 15 features:")
        for i, (fname, imp) in enumerate(feat_imp[:15], 1):
            print(f"    {i:2d}. {fname:<30} {imp:.4f}")

        # Gradient Boosting
        print("\n--- Model 4: sklearn GradientBoosting ---")
        gb_sk = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", GradientBoostingClassifier(
                n_estimators=50, max_depth=3, learning_rate=0.1,
                random_state=args.seed
            )),
        ])
        cv_scores_gb = cross_val_score(gb_sk, X, y, cv=skf, scoring="accuracy")
        print(f"  CV Accuracy: {cv_scores_gb.mean():.3f} (+/- {cv_scores_gb.std()*2:.3f})")

        y_pred_gb = cross_val_predict(gb_sk, X, y, cv=skf)
        m_gb = evaluate(y, y_pred_gb, None)
        avg_sklearn_gb = {k: m_gb[k] for k in ["accuracy", "precision", "recall", "f1", "roc_auc"] if k in m_gb}
        print(f"  Overall: ACC={m_gb['accuracy']:.3f} F1={m_gb['f1']:.3f}"
              f" ROC-AUC={m_gb.get('roc_auc', 'N/A')}")

        # Logistic Regression
        print("\n--- Model 5: sklearn LogisticRegression ---")
        lr_sk = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(
                max_iter=1000, C=1.0, random_state=args.seed,
                class_weight="balanced"
            )),
        ])
        cv_scores_lr = cross_val_score(lr_sk, X, y, cv=skf, scoring="accuracy")
        print(f"  CV Accuracy: {cv_scores_lr.mean():.3f} (+/- {cv_scores_lr.std()*2:.3f})")

        y_pred_lr = cross_val_predict(lr_sk, X, y, cv=skf)
        m_lr = evaluate(y, y_pred_lr, None)
        avg_sklearn_lr = {k: m_lr[k] for k in ["accuracy", "precision", "recall", "f1", "roc_auc"] if k in m_lr}
        print(f"  Overall: ACC={m_lr['accuracy']:.3f} F1={m_lr['f1']:.3f}"
              f" ROC-AUC={m_lr.get('roc_auc', 'N/A')}")

    # ─── Summary ────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  MODEL COMPARISON SUMMARY")
    print(f"{'='*60}")

    models_summary = [
        ("DecisionStump (baseline)", avg_stump),
        ("RandomForest (pure, 20t)", avg_rf),
    ]
    if avg_sklearn_rf:
        models_summary.append(("sklearn RandomForest (100t)", avg_sklearn_rf))
    if avg_sklearn_gb:
        models_summary.append(("sklearn GradientBoosting", avg_sklearn_gb))
    if avg_sklearn_lr:
        models_summary.append(("sklearn LogisticRegression", avg_sklearn_lr))

    print(f"  {'Model':<35} {'ACC':>6} {'PREC':>6} {'REC':>6} {'F1':>6} {'AUC':>6}")
    print(f"  {'─'*35} {'─'*6} {'─'*6} {'─'*6} {'─'*6} {'─'*6}")

    best_model_name = ""
    best_acc = 0

    for name, metrics in models_summary:
        auc_str = f"{metrics.get('roc_auc', 'N/A'):.3f}" if metrics.get("roc_auc") is not None else "N/A"
        print(f"  {name:<35} {metrics['accuracy']:.3f} {metrics['precision']:.3f} "
              f"{metrics['recall']:.3f} {metrics['f1']:.3f} {auc_str}")
        if metrics["accuracy"] > best_acc:
            best_acc = metrics["accuracy"]
            best_model_name = name

    print(f"\n  Best: {best_model_name} (ACC={best_acc:.3f})")

    # ─── Train final model on ALL data ──────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  TRAINING FINAL MODEL ON ALL DATA")
    print(f"{'='*60}")

    if use_sklearn:
        # Use sklearn RandomForest as final model
        final_model = Pipeline([
            ("scaler", StandardScaler()),
            ("clf", RandomForestClassifier(
                n_estimators=100, max_depth=6, min_samples_split=5,
                random_state=args.seed, class_weight="balanced"
            )),
        ])
        final_model.fit(X, y)

        # Feature importance
        importances = final_model.named_steps["clf"].feature_importances_
        feat_imp = {feature_cols[i]: round(float(importances[i]), 4)
                    for i in range(len(feature_cols))}

        # Save model
        model_path = os.path.join(args.output_dir, "model.pkl")
        with open(model_path, "wb") as f:
            pickle.dump({
                "model": final_model,
                "feature_cols": feature_cols,
                "metadata": {
                    "n_samples": n_total,
                    "n_features": len(feature_cols),
                    "n_positive": n_pos,
                    "n_negative": n_neg,
                    "cv_folds": args.cv_folds,
                    "best_model": best_model_name,
                    "best_acc": best_acc,
                    "seed": args.seed,
                },
            }, f)
        print(f"  Saved: {model_path}")
    else:
        # Pure Python fallback
        final_model = PureRandomForest(
            n_trees=30, max_depth=5, min_samples_split=5,
            random_state=args.seed
        )
        final_model.fit(X, y)

        feat_imp = final_model.feature_importances
        feat_imp = {feature_cols[int(k)] if int(k) < len(feature_cols) else f"feat_{k}": round(v, 4)
                    for k, v in feat_imp.items()}

        model_path = os.path.join(args.output_dir, "model.pkl")
        with open(model_path, "wb") as f:
            pickle.dump({
                "model": final_model,
                "feature_cols": feature_cols,
                "pure_python": True,
                "metadata": {
                    "n_samples": n_total,
                    "n_features": len(feature_cols),
                    "n_positive": n_pos,
                    "n_negative": n_neg,
                    "cv_folds": args.cv_folds,
                    "best_model": best_model_name,
                    "best_acc": best_acc,
                    "seed": args.seed,
                },
            }, f)
        print(f"  Saved: {model_path}")

    # ─── Save metrics ───────────────────────────────────────────────────
    metrics_path = os.path.join(args.output_dir, "model_metrics.json")
    metrics_data = {
        "n_samples": n_total,
        "n_positive": n_pos,
        "n_negative": n_neg,
        "cv_folds": args.cv_folds,
        "seed": args.seed,
        "best_model": best_model_name,
        "best_accuracy": best_acc,
        "models": {name: metrics for name, metrics in models_summary},
    }
    with open(metrics_path, "w", encoding="utf-8") as f:
        json.dump(metrics_data, f, indent=2)
    print(f"  Saved: {metrics_path}")

    # ─── Save feature importance ────────────────────────────────────────
    fi_path = os.path.join(args.output_dir, "feature_importance.json")
    fi_sorted = sorted(feat_imp.items(), key=lambda x: x[1], reverse=True)
    fi_data = [{"rank": i+1, "feature": name, "importance": imp}
               for i, (name, imp) in enumerate(fi_sorted)]
    with open(fi_path, "w", encoding="utf-8") as f:
        json.dump(fi_data, f, indent=2)
    print(f"  Saved: {fi_path}")

    print(f"\n{'='*60}")
    print(f"  DONE! Best model: {best_model_name} (ACC={best_acc:.3f})")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
