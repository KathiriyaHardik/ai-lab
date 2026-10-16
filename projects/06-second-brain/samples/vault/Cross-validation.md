---
sample: fictional note for testing, not real
tags: [ml]
---
# Cross-validation

k-fold cross-validation splits the data into k parts. Each part is the test set once. Common values are k = 5 or k = 10.

It gives a more honest score than a single train/test split and helps to spot [[Overfitting]]. For imbalanced classes, use stratified k-fold.
