#include <linux/module.h>
#include <linux/export-internal.h>
#include <linux/compiler.h>

MODULE_INFO(name, KBUILD_MODNAME);

__visible struct module __this_module
__section(".gnu.linkonce.this_module") = {
	.name = KBUILD_MODNAME,
	.init = init_module,
#ifdef CONFIG_MODULE_UNLOAD
	.exit = cleanup_module,
#endif
	.arch = MODULE_ARCH_INIT,
};



static const struct modversion_info ____versions[]
__used __section("__versions") = {
	{ 0xe8213e80, "_printk" },
	{ 0xbd03ed67, "page_offset_base" },
	{ 0xd82e1996, "set_memory_wb" },
	{ 0xb1ad3f2f, "boot_cpu_data" },
	{ 0x3e604df7, "__vma_start_write" },
	{ 0x767ccfe1, "remap_pfn_range" },
	{ 0x84f07bf7, "cachemode2protval" },
	{ 0x211f9d4e, "mem_section" },
	{ 0x7ec472ba, "__preempt_count" },
	{ 0xd272d446, "__SCT__preempt_schedule" },
	{ 0x5e865cb8, "pgprot_writecombine" },
	{ 0xd82e1996, "set_memory_uc" },
	{ 0xd82e1996, "set_memory_wc" },
	{ 0x653aa194, "class_create" },
	{ 0x9f222e1e, "alloc_chrdev_region" },
	{ 0xbd03ed67, "vmemmap_base" },
	{ 0x44decd6f, "hugetlb_optimize_vmemmap_key" },
	{ 0xd5f66efd, "cdev_init" },
	{ 0x8ea73856, "cdev_add" },
	{ 0xe486c4b7, "device_create" },
	{ 0x0040afbe, "param_ops_int" },
	{ 0x0040afbe, "param_ops_ulong" },
	{ 0xd272d446, "__fentry__" },
	{ 0xd272d446, "__x86_return_thunk" },
	{ 0x1595e410, "device_destroy" },
	{ 0x4e54d6ac, "cdev_del" },
	{ 0x0bc5fb0d, "unregister_chrdev_region" },
	{ 0xa1dacb42, "class_destroy" },
	{ 0xbebe66ff, "module_layout" },
};

static const u32 ____version_ext_crcs[]
__used __section("__version_ext_crcs") = {
	0xe8213e80,
	0xbd03ed67,
	0xd82e1996,
	0xb1ad3f2f,
	0x3e604df7,
	0x767ccfe1,
	0x84f07bf7,
	0x211f9d4e,
	0x7ec472ba,
	0xd272d446,
	0x5e865cb8,
	0xd82e1996,
	0xd82e1996,
	0x653aa194,
	0x9f222e1e,
	0xbd03ed67,
	0x44decd6f,
	0xd5f66efd,
	0x8ea73856,
	0xe486c4b7,
	0x0040afbe,
	0x0040afbe,
	0xd272d446,
	0xd272d446,
	0x1595e410,
	0x4e54d6ac,
	0x0bc5fb0d,
	0xa1dacb42,
	0xbebe66ff,
};
static const char ____version_ext_names[]
__used __section("__version_ext_names") =
	"_printk\0"
	"page_offset_base\0"
	"set_memory_wb\0"
	"boot_cpu_data\0"
	"__vma_start_write\0"
	"remap_pfn_range\0"
	"cachemode2protval\0"
	"mem_section\0"
	"__preempt_count\0"
	"__SCT__preempt_schedule\0"
	"pgprot_writecombine\0"
	"set_memory_uc\0"
	"set_memory_wc\0"
	"class_create\0"
	"alloc_chrdev_region\0"
	"vmemmap_base\0"
	"hugetlb_optimize_vmemmap_key\0"
	"cdev_init\0"
	"cdev_add\0"
	"device_create\0"
	"param_ops_int\0"
	"param_ops_ulong\0"
	"__fentry__\0"
	"__x86_return_thunk\0"
	"device_destroy\0"
	"cdev_del\0"
	"unregister_chrdev_region\0"
	"class_destroy\0"
	"module_layout\0"
;

MODULE_INFO(depends, "");


MODULE_INFO(srcversion, "2CE66C60754A578A23466C7");
