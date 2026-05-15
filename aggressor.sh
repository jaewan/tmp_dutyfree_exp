CORELIST="136,137,138,139,140,141,142,143,224,225,226,227,228,229,230,231"

for mode in wb_load wb_ntdqa wc_ntdqa uc_load; do
  for r in 1 2 3; do
    echo "=== $mode run $r ==="
    sudo ./bin/aggressor -m "$mode" -t 16 -c "$CORELIST" -s 256 -d 15 \
      | tee "results/exp1_exact_corelist16/${mode}_r${r}.txt"
  done
done

