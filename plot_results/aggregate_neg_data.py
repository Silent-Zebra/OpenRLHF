import pickle

neg_data = set()

base_dir = "/h/zhaostep/OpenRLHF/checkpoint/toyrlhfmulti"

# ls checkpoint/toyrlhfmulti  | grep -v pkl |  grep -v sddiv | grep mipr | grep neg_train | grep harml_ac
# for x in $(ls | grep pkl | grep mipr); do echo \"$x\",; done
neg_data_load_paths = [
"neg_data_aggregated_2025-04-06.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta100000.0_policy_ppo_epochs1_schedconstant_alr0.0001_clr0.0001_clossmse_policy_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta100000.0_policy_ppo_epochs1_schedconstant_alr0.0001_clr1e-06_clossmse_policy_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta100000.0_policy_ppo_epochs1_schedconstant_alr0.0003_clr0.0001_clossmse_policy_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta100000.0_policy_ppo_epochs1_schedconstant_alr0.0003_clr1e-05_clossmse_policy_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta100000.0_policy_ppo_epochs1_schedconstant_alr0.001_clr0.0001_clossmse_policy_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_reinforce_a0.001_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_blr3e-05_policy_psi_q_p_s_t_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_reinforce_a0.01_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_blr3e-05_policy_psi_q_p_s_t_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_reinforce_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_adambetas0.9_0.999_policy_psi_q_p_s_t_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.001_modulation_linear_head_ctl_epochs1_schedconstant_alr0.0003_blr3e-05_modulation_linear_head_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.001_modulation_linear_head_ctl_epochs1_schedconstant_alr0.001_blr3e-05_modulation_linear_head_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.001_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_blr3e-05_policy_psi_q_p_s_t_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.001_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_blr3e-05_policy_psi_q_p_s_t_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.01_modulation_linear_head_ctl_epochs1_schedconstant_alr0.0003_blr3e-05_modulation_linear_head_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.01_modulation_linear_head_ctl_epochs1_schedconstant_alr0.001_blr3e-05_modulation_linear_head_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.01_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_blr3e-05_policy_psi_q_p_s_t_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.01_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_blr3e-05_policy_psi_q_p_s_t_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_a0.01_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0003_blr3e-05_policy_psi_q_p_s_t_s1_thr-5.0.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_harml_neg_training_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_adambetas0.9_0.999_policy_psi_q_p_s_t_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_linear_head_ctl_epochs1_schedconstant_alr0.0001_adambetas0.9_0.999_modulation_linear_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_linear_head_ctl_epochs1_schedconstant_alr0.0003_adambetas0.9_0.999_modulation_linear_head_initheadbase_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_linear_head_ctl_epochs1_schedconstant_alr0.0003_adambetas0.9_0.999_modulation_linear_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_linear_head_ctl_epochs1_schedconstant_alr0.001_adambetas0.9_0.999_modulation_linear_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_linear_head_ctl_epochs1_schedconstant_alr0.003_adambetas0.9_0.999_modulation_linear_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_model_ctl_epochs1_schedconstant_alr0.0003_adambetas0.9_0.999_modulation_model_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_model_ctl_epochs1_schedconstant_alr3e-05_adambetas0.9_0.999_modulation_model_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_nn_head_ctl_epochs1_schedconstant_alr0.0001_adambetas0.9_0.999_modulation_nn_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_nn_head_ctl_epochs1_schedconstant_alr0.0003_adambetas0.9_0.999_modulation_nn_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_modulation_nn_head_ctl_epochs1_schedconstant_alr3e-05_adambetas0.9_0.999_modulation_nn_head_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0001_adambetas0.9_0.999_policy_psi_q_p_s_t_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr0.0003_adambetas0.9_0.999_policy_psi_q_p_s_t_sddiv1.0_s1.pkl",
"neg_data_toy_rlhf_Sm13In_remodev3lav2_miprAL_len20_beta-10.0_policy_psi_q_p_s_t_ctl_nosecondterm_epochs1_schedconstant_alr3e-05_adambetas0.9_0.999_policy_psi_q_p_s_t_sddiv1.0_s1.pkl",
]

for neg_data_load_path in neg_data_load_paths:
    with open(f"{base_dir}/{neg_data_load_path}", "rb") as f:
        neg_data_new = pickle.load(f)
    neg_data.update(neg_data_new)
    print("New neg_data")
    print(len(neg_data_new))
    print("Combined neg_data")
    print(len(neg_data))

# save
with open(f"{base_dir}/neg_data_aggregated.pkl", "wb") as f:
    pickle.dump(neg_data, f)