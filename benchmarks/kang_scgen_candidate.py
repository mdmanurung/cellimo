"""Frozen grounded candidate for the Kang scGen call benchmark.

This file records what an agent chose; it is not imported or executed by the
benchmark.  Model fitting is explicitly GPU-only so executing the candidate in
an appropriate scientific environment cannot silently fall back to CPU.
"""

import pertpy as pt
import scanpy as sc


# cellimo:source notebook:scverse_scanpy_tutorials_tutorial_pearson_residuals section=38 sha=ce152aa3ae32
train = pt.dt.kang_2018()
train.raw = train.copy()
sc.pp.normalize_total(train)

train_new = train[
    ~((train.obs["cell_type"] == "CD4 T cells") & (train.obs["label"] == "stim"))
].copy()


# cellimo:source notebook:theislab_pertpy_reproducibility_scgen_species section=6 sha=2ff3d7a15b20
pt.tl.Scgen.setup_anndata(train_new, batch_key="label", labels_key="cell_type")
model = pt.tl.Scgen(train_new)
model.train(
    max_epochs=100,
    batch_size=32,
    early_stopping=True,
    early_stopping_patience=25,
    accelerator="gpu",
    devices=1,
)
model.save("kang_scgen.pt", overwrite=True)


# cellimo:source notebook:scverse_scvi_tutorials_decipher_tutorial section=14 sha=a62e2f3160b3
latent = model.get_latent_representation()
latent_adata = sc.AnnData(X=latent, obs=train_new.obs.copy())
sc.pp.neighbors(latent_adata)
sc.tl.umap(latent_adata)
sc.pl.umap(latent_adata, color=["label", "cell_type"], frameon=False)

pred, _delta = model.predict(
    ctrl_key="ctrl",
    stim_key="stim",
    celltype_to_predict="CD4 T cells",
)
pred.obs["label"] = "pred"

ctrl = train[
    (train.obs["cell_type"] == "CD4 T cells") & (train.obs["label"] == "ctrl")
].copy()
stim = train[
    (train.obs["cell_type"] == "CD4 T cells") & (train.obs["label"] == "stim")
].copy()


# cellimo:source notebook:scverse_pertpy_tutorials_expression_prediction_evaluation section=26 sha=2cc765fcab1b
eval_adata = sc.concat([ctrl, stim, pred])


# cellimo:source notebook:scverse_scanpy_tutorials_tutorial_pearson_residuals section=42 sha=c97ed02b7c7f
sc.pp.pca(eval_adata, n_comps=50)


# cellimo:source notebook:scverse_scanpy_tutorials_day1_01_solutions section=106 sha=cdbb2053091c
sc.pl.pca_scatter(eval_adata, color="label")


# cellimo:source notebook:scverse_scanpy_tutorials_core section=56 sha=660928ab7ba1
cd4 = train[train.obs["cell_type"] == "CD4 T cells"].copy()
sc.tl.rank_genes_groups(cd4, groupby="label", method="wilcoxon")
response_genes = cd4.uns["rank_genes_groups"]["names"]["stim"]


# cellimo:source notebook:theislab_feature_attribution_sc_mask_scgen_roar_evaluation section=9 sha=ef96ab2e0684
model.reg_mean_plot(
    eval_adata,
    axis_keys={"x": "pred", "y": "stim"},
    labels={"x": "predicted", "y": "ground truth"},
    show=True,
    legend=False,
)
model.reg_mean_plot(
    eval_adata,
    axis_keys={"x": "pred", "y": "stim"},
    gene_list=response_genes[:10],
    labels={"x": "predicted", "y": "ground truth"},
    show=True,
    legend=False,
)


# cellimo:source notebook:scverse_decoupler_tutorials_rna_sc section=27 sha=24d0f5c291f0
sc.pl.violin(eval_adata, keys="ISG15", groupby="label")
