# ADR-003: Intervention Economics, P(Organic) Formulation & Evaluation Integrity

## Status
Accepted (Revised after Principal Review)

## Context
Payment recovery solutions routinely claim astronomical ROI by committing the **Gross Attribution Fallacy**: taking credit for all payments that succeed after a failure, even if the customer simply retried on their own without intervention.

Furthermore, naive systems overlook the hidden costs of aggressive dunning: SMS/WhatsApp fees, customer churn from communication fatigue, and model uncertainty.

Finally, an evaluation framework where LIFT wins under every synthetic scenario is scientifically suspect; a robust causal evaluation must include realistic conditions where LIFT's interventions lose to simpler baselines or choose to abstain.

## Decision
1. **Net Incremental Recovery Value (NIRV) as the Primary Objective:**
   All candidate scoring and decisioning optimizes for expected incremental recovery above the organic counterfactual, net of direct costs, customer friction, and model uncertainty penalties:
   $$\text{NIRV}(a, i) = \Big[ P(\text{Rec} \mid a, \mathbf{x}_i) - P(\text{Organic} \mid \mathbf{x}_i) \Big] \times \text{AmountAtRisk}(i) - \text{DirectCost}(a) - \text{FrictionCost}(a, c_i) - \text{RiskPenalty}(i)$$
   Where:
   - $\text{FrictionCost}(a, c_i) = \lambda_{\text{friction}} \times \text{AmountAtRisk}(i) \times \text{ContactFatigue}(c_i, a, t)$ ($\lambda_{\text{friction}} = 0.05$). Speculative `CustomerLTV` is replaced with the observable, honest buildathon proxy `AmountAtRisk(i)`.
   - $\text{RiskPenalty}(i) = \beta \times \text{Uncertainty}(i) \times \text{AmountAtRisk}(i)$ ($\beta = 0.10, \text{Uncertainty}(i) = 1.0 - \text{confidence\_score}(i)$).
2. **Explicit Scientific Categorization of $P(\text{Organic})$ (REQ-01):**
   The architecture distinguishes four explicit modalities:
   - **OBSERVED:** Direct empirical measurement from un-intervened holdout control splits (Baseline 0).
   - **ESTIMATED:** Calibrated statistical model prediction conditioned on failure category, rail, and customer covariates.
   - **CONFIGURED:** Merchant policy priors applied during cold start (`GLOBAL_FAILURE_PRIORS` dictionary; never an arbitrary undocumented constant).
   - **SIMULATED:** Independent causal Data-Generating Process (DGP) parameters strictly isolated in `lift.simulation.dgp` and inaccessible to production estimators.
3. **Prevention of Intervention Contamination & DGP Independence:**
   Historical training data is strictly right-censored at intervention dispatch timestamp ($t_{\text{exec}}$). Recoveries occurring post-intervention cannot be counted as organic without holdout control isolation. Production scoring models import zero DGP parameters.
4. **Closed-Form Computable Contact Fatigue Function (REQ-09):**
   Customer friction is computable directly from persistent customer state (`rolling_contacts_7d` and `last_contacted_at`):
   $$\text{ContactFatigue}(c, a, t) = w(a) \times \left(1.0 + \exp\left(-\frac{t - t_{\text{last}}}{48.0}\right) + 0.5 \times \text{rolling\_contacts\_7d}\right)$$
   For non-contact actions (`NO_ACTION`, `INTERNAL_RETRY_SCHEDULE`), $\text{ContactFatigue} = 0.0$.
5. **Pessimistic Test Cohorts in Evaluation:**
   Evaluation datasets explicitly feature cohorts where LIFT correctly loses to simpler baselines or abstains:
   - High Organic Recovery cohort ($P_{\text{org}} = 0.85$): LIFT selects `NO_ACTION` / `PASSIVE_WAIT`; if forced to intervene, loses heavily to Baseline 0.
   - Micro-Ticket cohort ($< ₹50$): LIFT suppresses paid outreach; Baseline 2 loses money.
   - Terminal Hard Declines: LIFT halts immediately; Baseline 1 wastes decline fees.
6. **Integer Subunit Currency Calculations:**
   All monetary amounts are stored and computed in 64-bit integer subunits (paise / cents).

## Consequences
### Positive:
- Protects merchants from paying dunning costs for revenue that would have recovered organically.
- Scientifically defensible evaluation: proves LIFT does not win merely because the simulator was designed to favor it.
- Non-linear contact fatigue prevents customer alienation and brand damage.

### Negative:
- Estimating $P(\text{Organic})$ requires maintaining un-intervened holdout cohorts (e.g. 20% holdout during onboarding).
- Requires more complex scoring and evaluation infrastructure than naive gross recovery trackers.
