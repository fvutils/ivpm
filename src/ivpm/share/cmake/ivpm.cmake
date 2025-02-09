

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
        list(APPEND "${incdir}" "${PACKAGES_DIR}/${pkgdir}/build/include")
        list(APPEND "${incdir}" "${PACKAGES_DIR}/${pkgdir}/src/include")
        set("${incdir}" "${${incdir}}" PARENT_SCOPE)
#        set("${incdir}" "${${incdir}}" PARENT_SCOPE)
        list(APPEND "${libdir}" "${PACKAGES_DIR}/${pkgdir}/build/lib")
        list(APPEND "${libdir}" "${PACKAGES_DIR}/${pkgdir}/build/lib64")
        set("${libdir}" "${${libdir}}" PARENT_SCOPE)
    else()
        message("Loading ${pkgdir} package info from a binary package")
        set(incdir "${pkgname}_INCDIR")
        set(libdir "${pkgname}_LIBDIR")

        # Hopefully using a binary package
        execute_process(
            COMMAND ${PYTHON} -m ivpm pkg-info incdirs ${pkgdir}
            OUTPUT_VARIABLE incdirs
            OUTPUT_STRIP_TRAILING_WHITESPACE
        )
        message("Set: ${incdir}=${incdirs}")
        list(APPEND "${incdir}" "${incdirs}")
        set("${incdir}" "${${incdir}}" PARENT_SCOPE)

        execute_process(
            COMMAND ${PYTHON} -m ivpm pkg-info libdirs ${pkgdir}
            OUTPUT_VARIABLE libdirs
            OUTPUT_STRIP_TRAILING_WHITESPACE
        )
        message("Set: ${libdir}=${libdirs}")
        list(APPEND "${libdir}" "${libdirs}")
        set("${libdir}" "${${libdir}}" PARENT_SCOPE)
    endif()
endfunction()
