# FraudShield AI dataset

## Included files

- fraudshield_upi_transactions.csv: 6004 behavioral transaction rows.
- fraudshield_scam_messages.csv: 2534 synthetic messages linked to transaction IDs where available.
- fraudshield_qr_events.csv: 1451 synthetic QR-decoding and merchant verification rows.
- fraudshield_data_dictionary.csv: column definitions.
- fraudshield_dataset.xlsx: workbook with summary, raw tables, split counts, and data dictionary.

## Why this is hackathon-ready

1. The transaction table supports a baseline classifier and a rule engine.
2. The message table supports scam-category classification and explanation text.
3. The QR table supports merchant mismatch, amount mismatch, and sticker-tampering checks.
4. Splits are assigned by user, reducing identity leakage between training and test.
5. Four demo rows are included: fake refund, fake KYC, QR mismatch, and a legitimate lunch payment.

## Recommended evaluation

Report precision, recall, F1, and PR-AUC for the fraud class. For the demo, prioritize recall for high-risk scams while monitoring false positives on the legitimate class.

## Public guidance

The synthetic patterns are paraphrased from public awareness guidance: NPCI Fraud Awareness (https://www.npci.org.in/fraud-awareness), NPCI UPI FAQs (https://www.npci.org.in/what-we-do/upi/faqs), and RBI BE(A)WARE material (https://cms.rbi.org.in/cms/assets/Documents/BEAWARE07032022.pdf).

## Safe-use note

This is a synthetic prototype dataset. It does not represent real prevalence, bank behavior, or production fraud controls. Do not use it to approve or decline real payments.

