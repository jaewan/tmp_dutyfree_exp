savedcmd_cxl_memtype.mod := printf '%s\n'   cxl_memtype.o | awk '!x[$$0]++ { print("./"$$0) }' > cxl_memtype.mod
