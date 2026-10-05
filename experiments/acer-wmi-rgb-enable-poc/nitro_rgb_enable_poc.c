// SPDX-License-Identifier: GPL-2.0-only
/*
 * Nitro Control AN515-58 RGB enable proof-of-concept.
 *
 * This is deliberately NOT a replacement for acer_wmi. It only reproduces
 * the two WMI calls used by the 2026 AN515-58 RGB RFC to poll the gaming
 * interface and enable all four keyboard zones.
 *
 * No autoload alias is provided. The module must be inserted manually.
 */

#include <linux/acpi.h>
#include <linux/dmi.h>
#include <linux/init.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/wmi.h>

#define ACER_GAMING_WMI_GUID "7A4DDFE7-5B5D-40B4-8595-4408E0CC7F56"

#define ACER_WMID_GET_GAMING_LED_METHODID      4
#define ACER_WMID_GET_GAMING_SYS_INFO_METHODID 5

#define ACER_GAMING_KBL_SET_ON        BIT_ULL(3)
#define ACER_GAMING_KBL_SET_ALL_ZONES GENMASK_ULL(43, 40)

static bool enable;
module_param(enable, bool, 0400);
MODULE_PARM_DESC(enable,
	"Actually issue the AN515-58 gaming-WMI RGB-enable sequence (default: false)");

static bool nitro_exact_model(void)
{
	const char *vendor = dmi_get_system_info(DMI_SYS_VENDOR);
	const char *product = dmi_get_system_info(DMI_PRODUCT_NAME);

	return vendor && product &&
	       !strcmp(vendor, "Acer") &&
	       !strcmp(product, "Nitro AN515-58");
}

static acpi_status nitro_wmi_call_u64(u32 method_id, u64 value)
{
	struct acpi_buffer input = {
		.length = sizeof(value),
		.pointer = &value,
	};
	struct acpi_buffer output = {
		.length = ACPI_ALLOCATE_BUFFER,
		.pointer = NULL,
	};
	acpi_status status;

	status = wmi_evaluate_method(ACER_GAMING_WMI_GUID, 0, method_id,
				     &input, &output);
	kfree(output.pointer);

	return status;
}

static int __init nitro_rgb_enable_poc_init(void)
{
	acpi_status status;
	u64 zone_enable = ACER_GAMING_KBL_SET_ON |
			  ACER_GAMING_KBL_SET_ALL_ZONES;

	if (!nitro_exact_model()) {
		pr_err("nitro_rgb_enable_poc: refusing non-AN515-58 hardware\n");
		return -ENODEV;
	}

	if (!wmi_has_guid(ACER_GAMING_WMI_GUID)) {
		pr_err("nitro_rgb_enable_poc: Acer gaming WMI GUID not present\n");
		return -ENODEV;
	}

	if (!enable) {
		pr_info("nitro_rgb_enable_poc: dry run only; model and WMI GUID validated\n");
		pr_info("nitro_rgb_enable_poc: reload with enable=1 to issue the test sequence\n");
		return 0;
	}

	pr_info("nitro_rgb_enable_poc: issuing gaming-system-info poll\n");
	status = nitro_wmi_call_u64(ACER_WMID_GET_GAMING_SYS_INFO_METHODID, 0);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 5 failed: %s\n",
		       acpi_format_exception(status));
		return -EIO;
	}

	pr_info("nitro_rgb_enable_poc: enabling all four keyboard zones (0x%llx)\n",
		zone_enable);
	status = nitro_wmi_call_u64(ACER_WMID_GET_GAMING_LED_METHODID,
				    zone_enable);
	if (ACPI_FAILURE(status)) {
		pr_err("nitro_rgb_enable_poc: method 4 failed: %s\n",
		       acpi_format_exception(status));
		return -EIO;
	}

	pr_info("nitro_rgb_enable_poc: WMI sequence completed; inspect keyboard visually\n");
	return 0;
}

static void __exit nitro_rgb_enable_poc_exit(void)
{
	pr_info("nitro_rgb_enable_poc: unloaded (firmware state is not changed on unload)\n");
}

module_init(nitro_rgb_enable_poc_init);
module_exit(nitro_rgb_enable_poc_exit);

MODULE_AUTHOR("Nitro Control project");
MODULE_DESCRIPTION("AN515-58 one-shot RGB enable experiment");
MODULE_LICENSE("GPL");
