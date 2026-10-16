---
sample: fictional note for testing, not real
tags: [ml]
---
# Precision and recall

Precision: of everything the model flagged, how much was right? TP / (TP + FP).
Recall: of everything it should have found, how much did it find? TP / (TP + FN).

F1 is the harmonic mean of the two. Use it when the classes are imbalanced, because accuracy hides the mistakes on the small class.
