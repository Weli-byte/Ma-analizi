# Possible duplicate clubs (for approval)

Pairs of the same country that look like one club. Pairs that played each other are already removed.
To merge a pair run `python -m src.markets.club_duplicates --apply DROP_ID=KEEP_ID`.

| Country | Keep (matches) | Merge into it (matches) | Why |
|---|---|---|---|
| AUT | Sturm Graz `AUT_sturm_graz` (225) | SK Sturm Graz `AUT_sk_sturm_graz` (8) | same name apart from club-form words |
| AZE | Qarabağ FK `AZE_qarabag_fk` (40) | Qarabağ Ağdam FK `AZE_qarabag_agdam_fk` (10) | one name is the start of the other |
| CRO | Dinamo Zagreb `CRO_dinamo_zagreb` (42) | GNK Dinamo Zagreb `CRO_gnk_dinamo_zagreb` (8) | same name apart from club-form words |
| CZE | Slavia Praha `CZE_slavia_praha` (52) | SK Slavia Praha `CZE_sk_slavia_praha` (8) | same name apart from club-form words |
| NED | AZ Alkmaar `NED_az_alkmaar` (171) | AZ `NED_az` (109) | one name is the start of the other |
| NED | FC Twente `NED_fc_twente` (138) | FC Twente '65 `NED_fc_twente_65` (108) | one name is the start of the other |
| NED | Feyenoord `NED_feyenoord` (158) | Feyenoord Rotterdam `NED_feyenoord_rotterdam` (126) | one name is the start of the other |
| NED | NEC `NED_nec` (108) | NEC Nijmegen `NED_nec_nijmegen` (68) | one name is the start of the other |
| NED | PSV Eindhoven `NED_psv_eindhoven` (156) | PSV `NED_psv` (137) | one name is the start of the other |
| NED | Willem II `NED_willem_ii` (94) | Willem II Tilburg `NED_willem_ii_tilburg` (41) | one name is the start of the other |
| POR | Boavista `POR_boavista` (136) | Boavista FC `POR_boavista_fc` (68) | same name apart from club-form words |
| POR | GD Estoril Praia `POR_gd_estoril_praia` (109) | GD Estoril `POR_gd_estoril` (68) | one name is the start of the other |
| POR | Gil Vicente `POR_gil_vicente` (136) | Gil Vicente FC `POR_gil_vicente_fc` (107) | same name apart from club-form words |
| SVK | Slovan Bratislava `SVK_slovan_bratislava` (22) | ŠK Slovan Bratislava `SVK_sk_slovan_bratislava` (8) | same name apart from club-form words |
| UKR | Shakhtar Donetsk `UKR_shakhtar_donetsk` (34) | FK Shakhtar Donetsk `UKR_fk_shakhtar_donetsk` (14) | same name apart from club-form words |
