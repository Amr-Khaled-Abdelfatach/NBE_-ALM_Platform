# FAQ

Questions an engineering review is likely to raise, with direct answers.

## About the numbers

??? question "Does the AI calculate anything?"
    No. Every number comes from Python. The model reads the results and writes text, and the prompt forbids new numbers. See [AI Assistant](ai.md).

??? question "How is NII computed?"
    Per position: balance × rate × days ÷ 365 over the horizon, summed as (loan + investment interest) − (deposit + funding interest). See [Calculation Engine](calculation-engine.md).

??? question "Is NII annual or for the horizon?"
    For the horizon. NIM and cost of funds are annualised.

??? question "What happens to a rate shock on a fixed-rate loan?"
    Nothing until it matures. If it matures inside the horizon it is assumed to roll over and reprice at that date.

??? question "Why does a rate shock not change a position until some date?"
    The shock applies after the position's first repricing event, so the impact is proportional to the time left in the horizon.

??? question "Is there a yield curve or pass-through?"
    No. One parallel shift is applied. It is the main modelling limitation. See [Limitations](limitations.md).

??? question "What does the *Eligible* column mean?"
    The position has a repricing event inside the horizon. It is not a flag for floating rate.

## About liquidity and risk

??? question "Is the LCR the Basel LCR?"
    No. The headline is HQLA ÷ deposits. The 30-day stressed outflow is computed but not used. The risk rule compares the proxy with 100 %.

??? question "Why does deposit run-off improve my results?"
    Deposits leaving reduce interest expense and shrink the deposit base used in the LCR proxy, and the model does not charge a replacement funding cost. This is a known limitation, not a feature.

??? question "Why is the rating HIGH on the base case?"
    The proxy is below the 100 % limit, so the rule returns HIGH. On the sample database the base case shows LCR 79.5 % and overall risk HIGH. The rating then says little about the scenario itself.

??? question "What does inflation do?"
    Nothing to the KPIs. It is stored, shown, and passed to the AI.

## About the data

??? question "Which database does it need?"
    MySQL 8 with seven tables and two views. It never creates objects. See [Database](database.md).

??? question "Why two SQL views?"
    They join products and market rates and convert everything to EGP, so Python receives one clean row per position and per cash flow.

??? question "What if a view is out of date?"
    The loader compares its row count with the source table. If they differ, it uses an equivalent query and shows a note.

??? question "How fresh is the data?"
    Cached for 120 seconds. **Reload data** forces a refresh.

## About use

??? question "Can I compare scenarios?"
    Yes. Saved scenarios can be compared on the Scenarios page, and the dashboard attribution table compares drivers inside one scenario.

??? question "Can I rerun a saved scenario with new data?"
    Yes, with run mode *Rerun Saved Scenario*, or by selecting it on the dashboard, which re-evaluates it on current data.

??? question "How do I describe a scenario in words?"
    Use *Free Type Scenario*, for example `Interest rates +300 bps; USD/EGP +15%; deposit outflows 10%`. Check the extracted table before running.

??? question "Why does the Excel not match the screen?"
    It uses its own formulas. In edge cases (blank or past repricing dates, rollover) it can differ. The engine is the reference. See [Reports](reports.md).

??? question "Is it ready for production?"
    It is a prototype. Authentication, tests, a Basel-style LCR and a yield curve would be the main steps. See the roadmap in [Limitations](limitations.md).

??? question "Is this an official NBE application?"
    No. It carries an NBE-inspired theme, and any use of the NBE name and logo needs the bank's permission.
