

function(Ivpm_AddPythonExtProject pkgdir pkgname)
    if (NOT PACKAGES_DIR)
        message(SEND_ERROR "PACKAGES_DIR not set when configuring package ${pkgdir}")
    endif()

    if (NOT PYTHON)
        message(SEND_ERROR "PYTHON not set when configuring package ${pkgdir}")
    endif()

    if (EXISTS "${PACKAGES_DIR}/${pkgdir}")
        message("Found package ${pkgdir} in ${PACKAGES_DIR}")
        set(incdir "${pkgname}_INCDIR")
        set(libdir "${pkgname}_LIBDIR")

        message("Set: ${incdir}=${PACKAGES_DIR}/${pkgdir}/src/include")
        set("${incdir}" "${PACKAGES_DIR}/${pkgdir}/src/include" PARENT_SCOPE)
#        set("${incdir}" "${${incdir}}" PARENT_SCOPE)
        list(APPEND "${libdir}" "${PACKAGES_DIR}/${pkgdir}/build/lib")
        set("${libdir}" "${${libdir}}")
    else()
        message("Failed to find package ${pkgdir} in ${PACKAGES_DIR}")
    endif()
endfunction()
