#!/bin/bash
if [ "$#" -eq 0 ]; then
    echo "Error: Please provide the training command"
    exit 1
fi

# Store the full command
COMMAND="$*"

# Extract parameters using awk
PARAMS=$(echo "$COMMAND" | awk '
{
    # Initialize empty variables
    micro_train = train = micro_rollout = rollout = ""
    max_epochs = num_episodes = num_episodes_h = gen_max_len = actor_lr = critic_lr = baseactor_lr = ""
    target_beta = lr_sched = actor_loss = kl = do_harmlessness = ""
    custom_prompt = prompt_data = parameterization = adam_beta2 = rm_type = dup_rollout = pretrain = reward_pretrain = init_head_from_base = ""
    sd_divider = harmloss = harmlossreinbaseline = hlrbval = ""
    save_negdata_threshold = threshold = alpha = only_eval_neg = use_base_as_proposal = ""

    # Scan through all matches in the string
    for(i=1; i<=NF; i++) {
        if($i == "--micro_train_batch_size") micro_train = $(i+1)
        if($i == "--train_batch_size") train = $(i+1)
        if($i == "--micro_rollout_batch_size") micro_rollout = $(i+1)
        if($i == "--rollout_batch_size") rollout = $(i+1)
        if($i == "--max_epochs") max_epochs = $(i+1)
        if($i == "--num_episodes") num_episodes = $(i+1)
        if($i == "--harmlessness_training_num_episodes") num_episodes_h = $(i+1)
        if($i == "--generate_max_len") gen_max_len = $(i+1)
        if($i == "--actor_learning_rate") actor_lr = $(i+1)
        if($i == "--critic_learning_rate") critic_lr = $(i+1)
        if($i == "--base_actor_learning_rate") baseactor_lr = "_baselr"$(i+1)
        # if($i == "--target_dist_beta") target_beta = "_beta"$(i+1)
        if($i ~ /^--target_dist_beta(=|$)/) target_beta = ($i ~ /=/) ? gensub(/^[^=]+=/, "", "g", $i) : "_beta"$(i+1)
        if($i ~ /^--save_negdata_threshold(=|$)/) save_negdata_threshold = ($i ~ /=/) ? "_savethr" gensub(/^[^=]+=/, "", "g", $i) : "_savethr" $(i+1)
        if($i ~ /^--threshold(=|$)/) threshold = ($i ~ /=/) ? "_thresh" gensub(/^[^=]+=/, "", "g", $i) : "_thresh" $(i+1)
        if($i == "--lr_scheduler") lr_sched = $(i+1)
        if($i == "--actor_loss_type") actor_loss = $(i+1)
        if($i == "--custom_single_prompt") custom_prompt = "_custom"
        if($i == "--parameterization") parameterization = $(i+1)
        if($i == "--adam_betas") adam_beta2 = "_adambeta2_"$(i+2)
        if($i == "--rm_type") rm_type = $(i+1)
        if($i == "--duplicate_rollout_batch_by") dup_rollout = "_"$(i+1)
        if($i == "--pretrain") {
		abbrev = substr($(i+1), 1, 2)
            pretrain = abbrev
        }
        if($i == "--reward_pretrain") {
		abbrev = substr($(i+1), 1, 2)
            reward_pretrain = abbrev
        }
        if($i == "--prompt_data") prompt_data = gensub("_.*", "", "g", gensub(".*/", "", "g", $(i+1)))
        if($i == "--init_head_from_base") init_head_from_base = "_initheadbase"
        if($i == "--additional_sd_divider") sd_divider = "_sddivider"$(i+1)
        if($i == "--harmlessness_training_loss_type") harmloss = "_harml"$(i+1)
        if($i == "--reinforce_baseline_type") harmlossreinbaseline = "_"$(i+1)
        if($i == "--reinforce_hardcoded_baseline") hlrbval = "_"$(i+1)
        if($i == "--alpha") alpha = "_alpha"$(i+1)
        if($i == "--init_kl_coef") kl = "_kl"$(i+1)
        if($i == "--only_evaluate_on_neg_data") only_eval_neg = "_onlyevalneg"
        if($i == "--do_harmlessness_training") do_harmlessness = 1
        if($i == "--use_base_as_proposal") use_base_as_proposal = "_baseprop"
    }
    # Use num_episodes_h if do_harmlessness is set
    episodes_to_use = do_harmlessness ? num_episodes_h : num_episodes

        print micro_train "|" train "|" micro_rollout "|" rollout "|" max_epochs "|" episodes_to_use "|" \
              gen_max_len "|" actor_lr "|" critic_lr "|" baseactor_lr "|" target_beta "|" save_negdata_threshold "|" threshold "|" lr_sched "|" \
              actor_loss "|" custom_prompt "|" parameterization "|" adam_beta2 "|" rm_type "|" dup_rollout "|" pretrain "|" \
              reward_pretrain "|" prompt_data "|" init_head_from_base "|" sd_divider "|" harmloss "|" harmlossreinbaseline "|" hlrbval "|" alpha "|" kl "|" only_eval_neg "|" use_base_as_proposal \

}')

# Read using the special delimiter
IFS='|' read MICRO_TRAIN TRAIN MICRO_ROLLOUT ROLLOUT MAX_EPOCHS NUM_EPISODES GEN_MAX_LEN \
    ACTOR_LR CRITIC_LR BASEACTOR_LR TARGET_BETA SAVE_NEGDATA_THRESH THRESH LR_SCHED ACTOR_LOSS CUSTOM_PROMPT PARAMETERIZATION ADAM_BETA2 RM_TYPE DUP_ROLLOUT PRETRAIN REWARD_PRETRAIN PROMPT_DATA \
    INITHEADBASE SD_DIVIDER HARMLOSS HARMLOSSREINBASELINE HLRBVAL ALPHA KL ONLY_EVAL_NEG BASE_PROP <<< "$PARAMS"


# echo $PRETRAIN
# PRETRAIN="${PRETRAIN%%/*}"
# echo $PRETRAIN


# Get current date in required format
CURRENT_DATE=$(date +%Y-%m-%d-%H-%M)

# Generate output filename
PATTERN="${CURRENT_DATE}${ONLY_EVAL_NEG}_${PRETRAIN}_${REWARD_PRETRAIN}_${PROMPT_DATA}_${RM_TYPE}${BASE_PROP}${THRESH}${TARGET_BETA}${KL}_len${GEN_MAX_LEN}_${PARAMETERIZATION}${INITHEADBASE}${SD_DIVIDER}_batch${MICRO_TRAIN}_${TRAIN}_${MICRO_ROLLOUT}_${ROLLOUT}${DUP_ROLLOUT}_epo${MAX_EPOCHS}_epi${NUM_EPISODES}${HARMLOSS}${HARMLOSSREINBASELINE}${HLRBVAL}${ALPHA}${BASEACTOR_LR}_${ACTOR_LOSS}_alr${ACTOR_LR}_clr${CRITIC_LR}_${LR_SCHED}${CUSTOM_PROMPT}${SAVE_NEGDATA_THRESH}"
SBATCH_FILE="sbatch_${PATTERN}"
OUTPUT_FILE="result_${PATTERN}_s1.txt"


# Create the sbatch file
cat > "$SBATCH_FILE" << EOL
#!/bin/bash
#SBATCH -J s1_$(($RANDOM % 100000))
#SBATCH --ntasks=1
#SBATCH --mem=48G
#SBATCH -c 4
#SBATCH --time=4:00:00
#SBATCH --partition=ml
#SBATCH --qos=ml
#SBATCH --account=ml
#SBATCH --nodelist=overture,quartet[1-2],quartet5
#SBATCH --nodes=1
#SBATCH --export=ALL
#SBATCH --output=$OUTPUT_FILE
#SBATCH --gres=gpu:1
ln -s /usr/bin/gcc-10 .local/bin/gcc
ln -s /usr/bin/g++-10 .local/bin/g++
export PATH=\$HOME/.local/bin/:\$PATH
cd ~/OpenRLHF
source newenv/bin/activate
export CUDA_HOME=/pkgs/cuda-12.4
export PATH=\$CUDA_HOME/bin:\$PATH
export LD_LIBRARY_PATH=\$CUDA_HOME/lib64:\$LD_LIBRARY_PATH
export MAX_JOBS=1
deepspeed --master_port $(($RANDOM % 1000 + 3000))1 $COMMAND
EOL

# Make the sbatch file executable
chmod +x "$SBATCH_FILE"
echo "Created sbatch file: $SBATCH_FILE"
echo "Output will be written to: $OUTPUT_FILE"
