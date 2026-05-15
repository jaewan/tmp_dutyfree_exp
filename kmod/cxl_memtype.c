/*
 * cxl_memtype.c — Kernel module for CXL UC/WC memory type experiments.
 *
 * Creates /dev/cxl_uc (uncacheable) and /dev/cxl_wc (write-combining).
 *
 * The physical range is split in half:
 *   First half  → direct map changed to UC → exposed via /dev/cxl_uc
 *   Second half → direct map changed to WC → exposed via /dev/cxl_wc
 *
 * set_memory_uc()/set_memory_wc() modify the kernel direct-map PTEs so
 * that remap_pfn_range() doesn't create a PAT conflict (which would
 * silently fall back to WB).  On exit, set_memory_wb() restores them.
 *
 * REQUIRES: target physical range must be offlined from System RAM first.
 *
 * Kernel: >= 6.4 (single-arg class_create, set_memory_* exported)
 * Load:   insmod cxl_memtype.ko cxl_phys_base=0x... cxl_phys_size=0x...
 */
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/fs.h>
#include <linux/cdev.h>
#include <linux/device.h>
#include <linux/mm.h>
#include <asm/io.h>
#include <asm/pgtable_types.h>
#include <asm/set_memory.h>

static unsigned long cxl_phys_base = 0;
static unsigned long cxl_phys_size = 0;
module_param(cxl_phys_base, ulong, 0444);
module_param(cxl_phys_size, ulong, 0444);
MODULE_PARM_DESC(cxl_phys_base, "Physical base of offlined CXL memory");
MODULE_PARM_DESC(cxl_phys_size, "Size in bytes of offlined CXL region");

static int allow_online = 0;
module_param(allow_online, int, 0444);
MODULE_PARM_DESC(allow_online, "Allow loading on online RAM (DANGEROUS)");

static unsigned long uc_phys_base, uc_size;
static unsigned long wc_phys_base, wc_size;
static int dm_uc_set, dm_wc_set;

#define DEV_NAME_UC  "cxl_uc"
#define DEV_NAME_WC  "cxl_wc"
#define CLASS_NAME   "cxl_memtype"

static struct class *cxl_class;
static struct cdev cdev_uc, cdev_wc;
static dev_t dev_uc, dev_wc;

static char *cxl_devnode(const struct device *dev, umode_t *mode)
{
    if (mode)
        *mode = 0666;
    return NULL;
}

/* ---- UC mmap ---- */
static int cxl_mmap_uc(struct file *filp, struct vm_area_struct *vma)
{
    unsigned long offset = vma->vm_pgoff << PAGE_SHIFT;
    size_t size = vma->vm_end - vma->vm_start;
    unsigned long pfn;

    if (offset > uc_size || size > uc_size - offset) {
        pr_err("cxl_memtype: UC mmap out of range (off=%lu sz=%zu max=%lu)\n",
               offset, size, uc_size);
        return -EINVAL;
    }

    pfn = (uc_phys_base + offset) >> PAGE_SHIFT;

    vma->vm_page_prot = pgprot_noncached(vma->vm_page_prot);
    vm_flags_set(vma, VM_IO | VM_PFNMAP | VM_DONTEXPAND | VM_DONTDUMP);

    if (remap_pfn_range(vma, vma->vm_start, pfn, size, vma->vm_page_prot)) {
        pr_err("cxl_memtype: UC remap_pfn_range failed\n");
        return -EAGAIN;
    }
    return 0;
}

/* ---- WC mmap ---- */
static int cxl_mmap_wc(struct file *filp, struct vm_area_struct *vma)
{
    unsigned long offset = vma->vm_pgoff << PAGE_SHIFT;
    size_t size = vma->vm_end - vma->vm_start;
    unsigned long pfn;

    if (offset > wc_size || size > wc_size - offset) {
        pr_err("cxl_memtype: WC mmap out of range (off=%lu sz=%zu max=%lu)\n",
               offset, size, wc_size);
        return -EINVAL;
    }

    pfn = (wc_phys_base + offset) >> PAGE_SHIFT;

    vma->vm_page_prot = pgprot_writecombine(vma->vm_page_prot);
    vm_flags_set(vma, VM_IO | VM_PFNMAP | VM_DONTEXPAND | VM_DONTDUMP);

    if (remap_pfn_range(vma, vma->vm_start, pfn, size, vma->vm_page_prot)) {
        pr_err("cxl_memtype: WC remap_pfn_range failed\n");
        return -EAGAIN;
    }
    return 0;
}

static const struct file_operations fops_uc = {
    .owner = THIS_MODULE,
    .mmap  = cxl_mmap_uc,
};

static const struct file_operations fops_wc = {
    .owner = THIS_MODULE,
    .mmap  = cxl_mmap_wc,
};

static int __init cxl_memtype_init(void)
{
    int ret;
    struct device *d;
    unsigned long dm_vaddr;
    int npages;

    if (!cxl_phys_base || !cxl_phys_size) {
        pr_err("cxl_memtype: must specify cxl_phys_base and cxl_phys_size\n");
        return -EINVAL;
    }
    if (cxl_phys_size < 2 * PAGE_SIZE) {
        pr_err("cxl_memtype: cxl_phys_size too small (need >= 2 pages for UC+WC split)\n");
        return -EINVAL;
    }

    pr_info("cxl_memtype: phys_base=0x%lx size=0x%lx (%lu MB)\n",
            cxl_phys_base, cxl_phys_size, cxl_phys_size >> 20);

    /* Safety: refuse if range looks like active RAM */
    {
        unsigned long pfn_s = cxl_phys_base >> PAGE_SHIFT;
        unsigned long pfn_e = (cxl_phys_base + cxl_phys_size - 1) >> PAGE_SHIFT;

        if (pfn_valid(pfn_s) || pfn_valid(pfn_e)) {
            struct page *ps = pfn_valid(pfn_s) ? pfn_to_page(pfn_s) : NULL;
            struct page *pe = pfn_valid(pfn_e) ? pfn_to_page(pfn_e) : NULL;

            if ((ps && page_count(ps) > 0) || (pe && page_count(pe) > 0)) {
                pr_err("cxl_memtype: REFUSING — range 0x%lx-0x%lx overlaps online RAM.\n",
                       cxl_phys_base, cxl_phys_base + cxl_phys_size);
                if (!allow_online)
                    return -EBUSY;
                pr_warn("cxl_memtype: allow_online=1 — proceeding despite risk!\n");
            }
        }
    }

    /* Split range: first half UC, second half WC */
    uc_phys_base = cxl_phys_base;
    uc_size      = (cxl_phys_size / 2) & PAGE_MASK;
    wc_phys_base = cxl_phys_base + uc_size;
    wc_size      = cxl_phys_size - uc_size;

    pr_info("cxl_memtype: UC range 0x%lx+0x%lx (%lu MB)\n",
            uc_phys_base, uc_size, uc_size >> 20);
    pr_info("cxl_memtype: WC range 0x%lx+0x%lx (%lu MB)\n",
            wc_phys_base, wc_size, wc_size >> 20);

    /* Change the direct-map PTEs from WB to UC for the UC half */
    dm_vaddr = (unsigned long)phys_to_virt(uc_phys_base);
    npages   = uc_size >> PAGE_SHIFT;
    ret = set_memory_uc(dm_vaddr, npages);
    if (ret) {
        pr_err("cxl_memtype: set_memory_uc failed (%d) — direct map unchanged\n", ret);
        return ret;
    }
    dm_uc_set = 1;
    pr_info("cxl_memtype: direct map set to UC for %d pages at virt 0x%lx\n",
            npages, dm_vaddr);

    /* Change the direct-map PTEs from WB to WC for the WC half */
    dm_vaddr = (unsigned long)phys_to_virt(wc_phys_base);
    npages   = wc_size >> PAGE_SHIFT;
    ret = set_memory_wc(dm_vaddr, npages);
    if (ret) {
        pr_err("cxl_memtype: set_memory_wc failed (%d) — restoring UC half to WB\n", ret);
        set_memory_wb((unsigned long)phys_to_virt(uc_phys_base),
                      (int)(uc_size >> PAGE_SHIFT));
        dm_uc_set = 0;
        return ret;
    }
    dm_wc_set = 1;
    pr_info("cxl_memtype: direct map set to WC for %d pages at virt 0x%lx\n",
            npages, dm_vaddr);

    /* Register devices */
    cxl_class = class_create(CLASS_NAME);
    if (IS_ERR(cxl_class)) {
        ret = PTR_ERR(cxl_class);
        goto err_restore;
    }
    cxl_class->devnode = cxl_devnode;

    ret = alloc_chrdev_region(&dev_uc, 0, 1, DEV_NAME_UC);
    if (ret) goto err_class;

    cdev_init(&cdev_uc, &fops_uc);
    ret = cdev_add(&cdev_uc, dev_uc, 1);
    if (ret) goto err_unreg_uc;

    d = device_create(cxl_class, NULL, dev_uc, NULL, DEV_NAME_UC);
    if (IS_ERR(d)) { ret = PTR_ERR(d); goto err_cdev_uc; }

    ret = alloc_chrdev_region(&dev_wc, 0, 1, DEV_NAME_WC);
    if (ret) goto err_dev_uc;

    cdev_init(&cdev_wc, &fops_wc);
    ret = cdev_add(&cdev_wc, dev_wc, 1);
    if (ret) goto err_unreg_wc;

    d = device_create(cxl_class, NULL, dev_wc, NULL, DEV_NAME_WC);
    if (IS_ERR(d)) { ret = PTR_ERR(d); goto err_cdev_wc; }

    pr_info("cxl_memtype: ready — /dev/%s (UC, %lu MB), /dev/%s (WC, %lu MB)\n",
            DEV_NAME_UC, uc_size >> 20, DEV_NAME_WC, wc_size >> 20);
    return 0;

err_cdev_wc:  cdev_del(&cdev_wc);
err_unreg_wc: unregister_chrdev_region(dev_wc, 1);
err_dev_uc:   device_destroy(cxl_class, dev_uc);
err_cdev_uc:  cdev_del(&cdev_uc);
err_unreg_uc: unregister_chrdev_region(dev_uc, 1);
err_class:    class_destroy(cxl_class);
err_restore:
    if (dm_wc_set) {
        set_memory_wb((unsigned long)phys_to_virt(wc_phys_base),
                      (int)(wc_size >> PAGE_SHIFT));
        dm_wc_set = 0;
    }
    if (dm_uc_set) {
        set_memory_wb((unsigned long)phys_to_virt(uc_phys_base),
                      (int)(uc_size >> PAGE_SHIFT));
        dm_uc_set = 0;
    }
    return ret;
}

static void __exit cxl_memtype_exit(void)
{
    device_destroy(cxl_class, dev_wc);
    cdev_del(&cdev_wc);
    unregister_chrdev_region(dev_wc, 1);
    device_destroy(cxl_class, dev_uc);
    cdev_del(&cdev_uc);
    unregister_chrdev_region(dev_uc, 1);
    class_destroy(cxl_class);

    if (dm_wc_set) {
        set_memory_wb((unsigned long)phys_to_virt(wc_phys_base),
                      (int)(wc_size >> PAGE_SHIFT));
        pr_info("cxl_memtype: restored WC range to WB\n");
    }
    if (dm_uc_set) {
        set_memory_wb((unsigned long)phys_to_virt(uc_phys_base),
                      (int)(uc_size >> PAGE_SHIFT));
        pr_info("cxl_memtype: restored UC range to WB\n");
    }

    pr_info("cxl_memtype: unloaded\n");
}

module_init(cxl_memtype_init);
module_exit(cxl_memtype_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("CXL UC/WC mapping with direct-map PAT fix");
