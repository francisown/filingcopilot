# Independent Validation Report: RetailScore v4.2

**Document control:** Synthetic demo document. Validation date: 2026-03-31. Model owner: Retail Credit Analytics. Validator: Independent Model Risk Management. Classification: internal demo data. This report is not a real validation opinion.

## 1. Executive summary

RetailScore v4.2 is a twelve-month probability-of-default model for retail unsecured lending. The validation team concludes that the model is **conditionally effective** for use in origination and portfolio monitoring, subject to the findings below. The model retains acceptable discrimination but shows material calibration drift in the near-prime segment. Two findings require remediation before the next annual validation: near-prime calibration and income data lineage.

## 2. Scope and approach

The review covered the model specification, data lineage, discrimination, calibration, stability, override logic, and governance. The validation sample contained 120,000 accounts originated between 2023-01-01 and 2025-12-31, with an out-of-time holdout from 2025-07-01 to 2025-12-31. Performance was measured on the out-of-time sample. The review did not cover SME lending, secured mortgage default, or macroeconomic stress testing.

## 3. Performance results

| Metric | Result | Validation threshold | Assessment |
|---|---:|---:|---|
| AUC | 0.71 | at least 0.70 | Pass |
| KS statistic | 0.28 | at least 0.25 | Pass |
| Population stability index (PSI) | 0.18 | below 0.25 | Watch |
| Near-prime observed PD | 6.8% | predicted 5.1% | Fail |
| Prime observed PD | 2.2% | predicted 2.0% | Pass |

The AUC of 0.71 and KS of 0.28 are above the minimum thresholds, so ranking power remains acceptable. The PSI of 0.18 is below the 0.25 trigger but is close enough to require quarterly monitoring. The near-prime segment is the main calibration concern: observed PD of 6.8% is 1.7 percentage points above the predicted 5.1%.

## 4. Findings

### F1 — Near-prime calibration drift (High)

Observed near-prime PD is 6.8% versus a predicted 5.1%, a relative under-prediction of 33%. The drift is concentrated in applicants with bureau scores between 620 and 660 and with two or more recent credit inquiries. The likely drivers are a change in the bureau population mix and stronger than expected deterioration after the 2025 repricing cycle. Required action: recalibrate the near-prime intercept and the bureau-score spline by 2026-06-30. Interim action: apply a 15% PD overlay to the near-prime segment until recalibration is complete. Owner: Retail Credit Analytics. Status: open.

### F2 — Income data lineage gap (High)

For 12% of applications, the verified income field is populated but the underlying document type and verification timestamp are missing. The bureau feed mapping also drops the file date for one bureau product. This prevents a complete reproduction of the income used in the decision. Required action: backfill the missing document metadata, fix the bureau mapping, and add a lineage completeness check to the monthly data quality report by 2026-05-31. Owner: Data Governance. Status: open.

### F3 — Override logic documentation (Medium)

The override reason codes are applied consistently, but the policy document does not define how overrides interact with the score cut-off. Required action: document the override decision tree and add it to the model inventory by 2026-04-30. Owner: Credit Policy. Status: closed on 2026-04-22.

### F4 — Monitoring threshold review (Low)

The current calibration monitoring threshold is a 20% relative deviation. The validation team recommends tightening it to 15% for the near-prime segment and adding a segment-level alert. Required action: update the monitoring specification by 2026-06-30. Owner: Model Risk Management. Status: open.

## 5. Model limitations

The model has four known limitations. First, it excludes SME and corporate exposures. Second, it relies on a static bureau snapshot and does not model bureau score volatility within the application month. Third, the development sample overlaps the COVID-era payment relief period, so the repayment behaviour of forbearance accounts may not generalise. Fourth, the model has no explicit macroeconomic overlay; stress effects are applied downstream in provisioning.

These limitations do not invalidate the model for origination, but they must be disclosed to users and reconsidered at the next validation.

## 6. Overall opinion and required actions

The model is conditionally effective. The near-prime PD overlay must remain in place until recalibration is validated. The income lineage gap must be closed before the next annual validation. The model owner must present a remediation plan to the Model Risk Committee within thirty days. The next independent validation is due by 2027-03-31. A trigger review is required earlier if PSI exceeds 0.25, if AUC falls below 0.66, or if near-prime observed PD exceeds 8.0% for two consecutive quarters.

## 7. Sign-off

- Independent Model Risk Management: validation lead (synthetic sign-off).
- Retail Credit Analytics: model owner (synthetic sign-off).
- Model Risk Committee: noted 2026-04-10 (synthetic record).
