#---------------flir--------------
#RGB-T
python tools/infer.py \
    -c configs/feardetr/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train1+2_from9.yml \
    -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train1+2_from9/best_model.pdparams \
    --dual_modal \
    --infer_img=datasets/align_flir/images/data_rgb/FLIR_09600_RGB.jpg \
    --infer_img_modal2=datasets/align_flir/images/data_ir/FLIR_09600_PreviewData.jpeg \
    --draw_threshold=0.5 \
    --save_results=False \
    --visualize=True \
    --output_dir=infer_output/flir/rgb-t2

# python tools/infer.py \
#     -c configs/feardetr/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train1+2_from9.yml \
#     -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train1+2_from9/best_model.pdparams \
#     --dual_modal \
#     --infer_img=datasets/align_flir/images/data_ir/FLIR_09600_PreviewData.jpeg \
#     --infer_img_modal2=datasets/align_flir/images/data_rgb/FLIR_09600_RGB.jpg \
#     --draw_threshold=0.5 \
#     --save_results=False \
#     --visualize=True \
#     --output_dir=infer_output/flir/t-rgb2

#RGB-RGB
python tools/infer.py \
    -c configs/feardetr/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train2_test_rgb.yml \
    -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train1+2_from9/best_model.pdparams \
    --dual_modal \
    --infer_img=datasets/align_flir/images/data_rgb/FLIR_00004_RGB.jpg \
    --infer_img_modal2=datasets/align_flir/images/data_rgb/FLIR_00004_RGB.jpg \
    --draw_threshold=0.5 \
    --save_results=False \
    --visualize=True \
    --output_dir=infer_output/flir/rgb-rgb1

#T-T
python tools/infer.py \
    -c configs/feardetr/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train2_test_ir.yml \
    -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_align_flir_v1_ir_X_class3_train1+2_from9/best_model.pdparams \
    --dual_modal \
    --infer_img=datasets/align_flir/images/data_ir/FLIR_00004_PreviewData.jpeg \
    --infer_img_modal2=datasets/align_flir/images/data_ir/FLIR_00004_PreviewData.jpeg \
    --draw_threshold=0.5 \
    --save_results=False \
    --visualize=True \
    --output_dir=infer_output/flir/t-t1

#---------------llvip--------------
#RGB-T
python tools/infer.py \
    -c configs/feardetr/feardetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
    -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9/best_model.pdparams \
    --dual_modal \
    --infer_img=datasets/LLVIP/images/data_ir/100111.jpg \
    --infer_img_modal2=datasets/LLVIP/images/data_rgb/100111.jpg \
    --draw_threshold=0.5 \
    --save_results=False \
    --visualize=True \
    --output_dir=infer_output/llvip/t-rgb2

# python tools/infer.py \
#     -c configs/feardetr/feardetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9.yml \
#     -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9/best_model.pdparams \
#     --dual_modal \
#     --infer_img=datasets/LLVIP/images/data_rgb/100111.jpg \
#     --infer_img_modal2=datasets/LLVIP/images/data_ir/100111.jpg \
#     --draw_threshold=0.5 \
#     --save_results=False \
#     --visualize=True \
#     --output_dir=infer_output/llvip/rgb-t2

#RGB-RGB
python tools/infer.py \
    -c configs/feardetr/feardetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train2_test_rgb.yml \
    -o weights=output_fear/flir/feardetr_hgnetv2_x_5x_LLVIP_ir_X_class1_train1+2_from9/best_model.pdparams \
    --dual_modal \
    --infer_img=datasets/LLVIP/images/data_rgb/010006.jpg \
    --infer_img_modal2=datasets/LLVIP/images/data_rgb/010006.jpg \
    --draw_threshold=0.5 \
    --save_results=False \
    --visualize=True \
    --output_dir=infer_output/llvip/rgb_only

#T-T


#GT
python tools/gt_infer.py