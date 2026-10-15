# Synthetic benchmark data

PaymentShield uses synthetic UPI examples for its hackathon prototype. No real customer records are included.

- Transactions: 6,004 rows, stored in transactions/part-001.csv through part-011.csv.
- Messages: 2,534 rows in fraudshield_scam_messages.csv.
- QR events: 1,451 rows in fraudshield_qr_events.csv.
- Column definitions: fraudshield_data_dictionary.csv.

The earlier single-file GitHub uploads were truncated. This version restores the complete records in smaller, reviewable files. The application combines the transaction parts automatically, and Risk intelligence can export a complete CSV.

The supplied split column groups transaction users. The language-model training code creates a separate normalized-template split to avoid evaluating on the same sentence with a different rupee amount. Hand-authored demo examples are excluded from fitting and evaluation.

Scam patterns were informed by public awareness guidance:

- [NPCI Fraud Awareness](https://www.npci.org.in/fraud-awareness)
- [NPCI UPI FAQs](https://www.npci.org.in/what-we-do/upi/faqs)
- [RBI BE(A)WARE](https://cms.rbi.org.in/cms/assets/Documents/BEAWARE07032022.pdf)

All amounts, device scores, history, and class prevalence reflect generator assumptions. They are not measured population statistics. Some synthetic ranges differ strongly by class, making the transaction classification task easier than real fraud detection. The messages come from a small English-language template library. Model results must be presented with these limitations.
