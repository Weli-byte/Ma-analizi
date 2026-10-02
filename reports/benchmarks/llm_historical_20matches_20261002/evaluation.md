## Leaderboard

No single overall winner: read every metric together.

### global

| model | class | scope | n | log_loss | brier | rps | ece_raw | accuracy |
|---|---|---|---|---|---|---|---|---|
| llm_gemini_gemini_3_1_flash_lite | LLM_REAL | global | 20 | 0.6787 | 0.3690 | 0.1544 | 0.2913 | 0.8500 |
| llm_groq_openai_gpt_oss_20b | LLM_REAL | global | 20 | 0.7824 | 0.4417 | 0.1845 | 0.2807 | 0.7000 |
| llm_openai_gpt_6_luna | LLM_REAL | global | 20 | 0.6770 | 0.3677 | 0.1540 | 0.2923 | 0.8500 |

### by_league

| model | class | scope | n | log_loss | brier | rps | ece_raw | accuracy |
|---|---|---|---|---|---|---|---|---|
| llm_gemini_gemini_3_1_flash_lite | LLM_REAL | league:EPL | 11 | 0.6466 | 0.3468 | 0.1474 | 0.3196 | 0.9091 |
| llm_gemini_gemini_3_1_flash_lite | LLM_REAL | league:LALIGA | 9 | 0.7180 | 0.3961 | 0.1629 | 0.3379 | 0.7778 |
| llm_groq_openai_gpt_oss_20b | LLM_REAL | league:EPL | 11 | 0.7642 | 0.4348 | 0.1820 | 0.2352 | 0.7273 |
| llm_groq_openai_gpt_oss_20b | LLM_REAL | league:LALIGA | 9 | 0.8048 | 0.4502 | 0.1876 | 0.4168 | 0.6667 |
| llm_openai_gpt_6_luna | LLM_REAL | league:EPL | 11 | 0.6486 | 0.3484 | 0.1483 | 0.3216 | 0.9091 |
| llm_openai_gpt_6_luna | LLM_REAL | league:LALIGA | 9 | 0.7119 | 0.3913 | 0.1611 | 0.3366 | 0.7778 |

### by_season

| model | class | scope | n | log_loss | brier | rps | ece_raw | accuracy |
|---|---|---|---|---|---|---|---|---|
| llm_gemini_gemini_3_1_flash_lite | LLM_REAL | season:2022-23 | 10 | 0.6225 | 0.3313 | 0.1360 | 0.4150 | 0.9000 |
| llm_gemini_gemini_3_1_flash_lite | LLM_REAL | season:2023-24 | 10 | 0.7350 | 0.4067 | 0.1727 | 0.2497 | 0.8000 |
| llm_groq_openai_gpt_oss_20b | LLM_REAL | season:2022-23 | 10 | 0.7303 | 0.4033 | 0.1666 | 0.4165 | 0.8000 |
| llm_groq_openai_gpt_oss_20b | LLM_REAL | season:2023-24 | 10 | 0.8346 | 0.4801 | 0.2023 | 0.2591 | 0.6000 |
| llm_openai_gpt_6_luna | LLM_REAL | season:2022-23 | 10 | 0.6239 | 0.3319 | 0.1366 | 0.4152 | 0.9000 |
| llm_openai_gpt_6_luna | LLM_REAL | season:2023-24 | 10 | 0.7302 | 0.4035 | 0.1715 | 0.2515 | 0.8000 |
