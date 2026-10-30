/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2010, Rice University
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Rice University nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#ifndef OMPL_BASE_PLAN_
#define OMPL_BASE_PLAN_

#include "ompl/multirobot/base/SpaceInformation.h"
#include "ompl/base/Path.h"

namespace ompl
{
    namespace multirobot
    {
        namespace base
        {
            OMPL_CLASS_FORWARD(SpaceInformation);
            OMPL_CLASS_FORWARD(Plan);
            /** \brief Abstract definition of a plan */
            class Plan
            {
            public:
                // non-copyable
                Plan(const Plan &) = delete;
                Plan &operator=(const Plan &) = delete;

                /** \brief Constructor. A path must always know the space information it is part of */
                Plan(SpaceInformationPtr si) : si_(std::move(si))
                {
                }

                /** \brief Destructor */
                virtual ~Plan() = default;

                /** \brief Get the space information associated to this class */
                const SpaceInformationPtr &getSpaceInformation() const
                {
                    return si_;
                }

                /** \brief Cast this instance to a desired type. */
                template <class T>
                const T *as() const
                {
                    /** \brief Make sure the type we are allocating is indeed a Plan */
                    BOOST_CONCEPT_ASSERT((boost::Convertible<T *, Plan *>));

                    return static_cast<const T *>(this);
                }

                /** \brief Cast this instance to a desired type. */
                template <class T>
                T *as()
                {
                    /** \brief Make sure the type we are allocating is indeed a Plan */
                    BOOST_CONCEPT_ASSERT((boost::Convertible<T *, Plan *>));

                    return static_cast<T *>(this);
                }

                /** \brief Return the length of a plan */
                virtual double length() const = 0;

                // /** \brief Return the cost of the path with respect to a
                //     specified optimization objective. */
                // virtual Cost cost(const OptimizationObjectivePtr &obj) const = 0;

                // /** \brief Check if the path is valid */
                // virtual bool check() const = 0;

                /** \brief Print the path to a stream */
                virtual void print(std::ostream &out, std::string prefix) const = 0;

            protected:
                /** \brief The multi-agent space information this plan is part of */
                SpaceInformationPtr si_;
            };
        }
    }
}

#endif
