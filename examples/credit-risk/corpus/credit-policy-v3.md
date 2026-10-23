# Retail Credit Policy v3.0

**Document control:** Synthetic demo document. Owner: Credit Policy Committee. Approved: 2026-01-15. Effective: 2026-02-01. Classification: internal demo data. This document is not a real bank policy and must not be used for lending decisions.

## 1. Purpose and scope

This policy sets the minimum credit standards for retail lending products: prime unsecured loans, near-prime unsecured loans, auto loans, and residential mortgages. It applies to new originations, limit increases, and material restructures. Product programs may add stricter rules but may not relax the limits in this document without an approved exception.

## 2. Definitions

- **DTI (debt-to-income):** total monthly debt service divided by verified gross monthly income.
- **LTV (loan-to-value):** outstanding principal divided by the latest independent collateral valuation.
- **DPD (days past due):** calendar days since the earliest unpaid instalment.
- **PD (probability of default):** twelve-month probability that an obligor becomes 90+ DPD or is charged off.
- **Forbearance:** a temporary change to payment terms granted to a borrower in financial difficulty.
- **Compensating factor:** a documented strength that offsets a policy exception, such as stable employment, substantial liquid reserves, or a long clean repayment history.

## 3. Eligibility limits

| Product | Maximum DTI | Maximum LTV | Minimum verified monthly income |
|---|---:|---:|---:|
| Prime unsecured loan | 43% | n/a | S$2,500 |
| Near-prime unsecured loan | 38% | n/a | S$2,500 |
| New auto loan | 45% | 110% | S$2,000 |
| Used auto loan | 45% | 95% | S$2,000 |
| Residential mortgage | 40% | 80% | S$3,000 |

The maximum DTI for a prime unsecured loan is 43%. The maximum LTV for a used auto loan is 95%. Limits are measured at origination using verified income and the latest valuation. Stated income may be used only for a preliminary decision and never for final approval.

## 4. Income verification

Every application requires documented income. Acceptable documents are:

1. Three consecutive recent payslips showing gross pay and employer name.
2. Six months of bank statements showing salary credits.
3. The latest annual tax notice or employment income statement.
4. An employer letter confirming role, tenure, and gross monthly income.

Self-employed applicants require two years of tax notices and six months of business bank statements. Rental income is accepted at 70% of the documented gross rent. Variable income is averaged over twelve months and capped at 100% of the documented average. Missing income documents are a policy exception, not a data-entry default.

## 5. Exception policy

An exception is allowed only when the application is otherwise strong and at least two compensating factors are documented. Acceptable compensating factors include:

- Twelve months of clean repayment history with the bank.
- Liquid reserves covering six months of total debt service.
- Employment tenure above five years in the same industry.
- A verified secondary income source.
- A lower-risk product or a smaller requested amount that brings residual risk within appetite.

The approval matrix for exceptions is:

| Excess over policy limit | Required approver |
|---|---|
| Up to 5% | Credit Manager |
| Above 5% and up to 10% | Head of Credit |
| Above 10% and up to 15% | Chief Risk Officer |
| Above 15% | Board Risk Committee |

The Chief Risk Officer must approve any exception above 10% over the policy limit. Exceptions above 15% require the Board Risk Committee. Every exception must record the policy limit, the requested value, the compensating factors, the approver, and the expiry date. Exceptions are reviewed monthly by the Credit Policy Committee.

## 6. Delinquency management

An account is delinquent when it is one day past due. It is classified as 30+ DPD after thirty calendar days and 90+ DPD after ninety calendar days. Collections must contact a 30+ DPD account within five business days. A 90+ DPD account is transferred to the recoveries unit unless a forbearance plan is approved.

Forbearance may last up to six months. A second forbearance within twelve months requires Head of Credit approval. Forbearance does not reset the delinquency history used for provisioning.

## 7. Model use and revalidation

The RetailScore PD model supports, but does not replace, credit judgement. A model override must be recorded with a reason code. The model is subject to:

- Independent validation at least annually.
- Quarterly monitoring of discrimination, calibration, and stability.
- A trigger review when the population stability index exceeds 0.25, when AUC falls by more than 0.05 from the validated value, or when the observed 90+ DPD rate exceeds the predicted rate by more than 30% for two consecutive quarters.

The model owner must maintain a model inventory entry, a documented data lineage map, and a list of known limitations. Revalidation results are reported to the Model Risk Committee.

## 8. Roles and responsibilities

- **Credit Policy Committee:** owns this policy and approves material changes.
- **Head of Credit:** owns first-line credit decisions and exception quality.
- **Chief Risk Officer:** owns the risk appetite and approves high exceptions.
- **Model Risk Management:** owns independent validation and model inventory.
- **Data Governance:** owns bureau and income data lineage.
- **Internal Audit:** reviews policy compliance and exception evidence.

## 9. Data lineage and record retention

Application, bureau, income, and decision data must be traceable to a source system and a retrieval timestamp. Bureau attributes must record the bureau file date. Income documents must record the document type, period, and verification status. Credit decisions and overrides are retained for seven years. Model inputs used in a decision must be reproducible from the retained record.

## 10. Review cycle

This policy is reviewed at least annually, or earlier after a material portfolio loss, a regulatory change, a model failure, or a change in risk appetite. The next scheduled review is 2027-02-01. Interim changes require Chief Risk Officer approval and ratification at the next Credit Policy Committee meeting.
